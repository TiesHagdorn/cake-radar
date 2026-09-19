"""Process one Slack message from initial checks through to an optional alert."""

import base64
from collections import OrderedDict, deque
from datetime import datetime
import io
import logging
import re
from threading import Lock
from typing import Dict, List
from zoneinfo import ZoneInfo

import requests
from PIL import Image
from pillow_heif import register_heif_opener

from . import ai_classifier
from .config import Config


processed_messages = deque(maxlen=1000)
evaluated_messages = {}
_MAX_MESSAGE_STATES = 1000
_message_states = OrderedDict()
_message_state_lock = Lock()
_channel_name_cache: Dict[str, str] = {}
_user_name_cache: Dict[str, str] = {}
_slack_app = None
_openai_client = None
_heif_registered = False
_PILLOW_TO_OPENAI = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp'}


def configure(slack_app, openai_client):
    """Set the clients used while processing Slack messages."""
    global _slack_app, _openai_client
    _slack_app = slack_app
    _openai_client = openai_client


def find_cake_words(text: str) -> List[str]:
    """Return configured cake words that appear as standalone terms."""
    return [
        keyword for keyword in Config.KEYWORDS
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text.lower(), re.IGNORECASE)
    ]


def _ensure_heif_registered():
    global _heif_registered
    if not _heif_registered:
        register_heif_opener()
        _heif_registered = True


def download_slack_images(files: list, max_images: int = 1) -> List[str]:
    """Download Slack image attachments as base64 data URIs for the AI."""
    data_uris = []
    for file_info in files:
        if len(data_uris) >= max_images:
            break
        mimetype = file_info.get('mimetype', '')
        url = file_info.get('url_private_download') or file_info.get('url_private')
        if not mimetype.startswith('image/') or not url:
            continue
        try:
            response = requests.get(
                url, headers={'Authorization': f'Bearer {Config.SLACK_BOT_TOKEN}'}, timeout=10, allow_redirects=False,
            )
            if response.is_redirect or response.is_permanent_redirect:
                redirect_url = response.headers.get('Location')
                if redirect_url:
                    response = requests.get(redirect_url, timeout=10)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                response = requests.get(url, timeout=10, allow_redirects=True)
                response.raise_for_status()
                content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                logging.warning(f"Slack returned {content_type!r} instead of image, skipping")
                continue
            _ensure_heif_registered()
            image = Image.open(io.BytesIO(response.content))
            output_format = image.format if image.format in _PILLOW_TO_OPENAI else 'JPEG'
            if output_format == 'JPEG':
                image = image.convert('RGB')
            buffer = io.BytesIO()
            image.save(buffer, output_format)
            encoded = base64.b64encode(buffer.getvalue()).decode('utf-8')
            data_uris.append(f"data:{_PILLOW_TO_OPENAI.get(image.format, 'image/jpeg')};base64,{encoded}")
        except Exception as error:
            logging.error(f"Failed to download Slack image: {error}")
    return data_uris


def _channel_name(channel_id: str) -> str:
    if channel_id not in _channel_name_cache:
        try:
            result = _slack_app.client.conversations_info(channel=channel_id)
            _channel_name_cache[channel_id] = '#' + result['channel']['name']
        except Exception:
            _channel_name_cache[channel_id] = channel_id
    return _channel_name_cache[channel_id]


def _user_name(user_id: str) -> str:
    if user_id not in _user_name_cache:
        try:
            result = _slack_app.client.users_info(user=user_id)
            profile = result['user']['profile']
            _user_name_cache[user_id] = '@' + (profile.get('display_name') or profile.get('real_name') or user_id)
        except Exception:
            _user_name_cache[user_id] = '@' + user_id
    return _user_name_cache[user_id]


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=ZoneInfo("Europe/Amsterdam")).strftime("%H:%M")
    except Exception:
        return ts


def _canonical_changed_message_ts(event: Dict) -> str:
    return (event.get('previous_message') or {}).get('ts') or (event.get('message') or {}).get('ts', '')


def _claim_message_evaluation(channel_id: str, ts: str, text: str, is_edit: bool) -> bool:
    key = (channel_id, ts)
    keywords = set(find_cake_words(text))
    if not keywords:
        return False
    with _message_state_lock:
        state = _message_states.setdefault(key, {'in_flight': False, 'evaluated': False, 'forwarded': False, 'keywords': set()})
        if state['in_flight'] or state['forwarded']:
            return False
        if state['evaluated'] and (not is_edit or not keywords - state['keywords']):
            return False
        state['in_flight'] = True
        _message_states.move_to_end(key)
        while len(_message_states) > _MAX_MESSAGE_STATES:
            _message_states.popitem(last=False)
        return True


def _complete_message_evaluation(channel_id: str, ts: str, text: str, forwarded: bool) -> None:
    key = (channel_id, ts)
    with _message_state_lock:
        state = _message_states.get(key)
        if state is None:
            return
        state['in_flight'] = False
        state['evaluated'] = True
        state['forwarded'] = state['forwarded'] or forwarded
        state['keywords'].update(find_cake_words(text))
        _message_states.move_to_end(key)


def notify_openai_operational_error(error: Exception, context: str):
    kind = ai_classifier.openai_operational_error_kind(error)
    if not kind:
        return
    if not Config.OPERATIONAL_ALERT_CHANNEL:
        logging.error("OpenAI operational alert suppressed: no OPERATIONAL_ALERT_CHANNEL configured")
        return
    detail = "OpenAI authentication failed." if kind == 'auth' else "OpenAI quota or billing failed."
    text = (
        f"Hi {Config.OPERATIONAL_ALERT_SUPPORT_MENTION}, I'm broken, please check the logs!\n"
        f"{detail} Treat alerts may be missed until this is fixed. Context: `{context}`."
    )
    try:
        _slack_app.client.chat_postMessage(channel=Config.OPERATIONAL_ALERT_CHANNEL, text=text)
    except Exception as slack_error:
        logging.error(f"Failed to send operational alert: {slack_error}")


def assess_certainty(message_text: str, image_data_uris: List[str] = None) -> Dict:
    return ai_classifier.assess_certainty(_openai_client, message_text, notify_openai_operational_error, image_data_uris)


def judge_decision(message_text: str, classifier_reason: str, image_data_uris: List[str] = None) -> Dict:
    return ai_classifier.judge_decision(_openai_client, message_text, classifier_reason, notify_openai_operational_error, image_data_uris)


def assess_message(text: str) -> Dict:
    """Assess one message without sending a Slack alert; used by the CLI."""
    matched_keywords = find_cake_words(text)
    if not matched_keywords:
        return {'matched_keywords': [], 'decision': 'no', 'total_certainty': 0, 'classifier_forwarded': False, 'forwarded': False}
    result = assess_certainty(text)
    classifier_forwarded = result['decision'] == 'yes' and result['total_certainty'] > Config.CERTAINTY_THRESHOLD
    judge = judge_decision(text, result.get('reason', '')) if classifier_forwarded else None
    return {
        **result,
        'matched_keywords': matched_keywords,
        'classifier_forwarded': classifier_forwarded,
        'judge': judge,
        'forwarded': classifier_forwarded and judge['verdict'] != 'overturn' if judge else False,
    }


def _is_public_source_channel(payload: Dict, channel_id: str) -> bool:
    channel_type = payload.get('channel_type') or payload.get('message', {}).get('channel_type')
    is_public = channel_type == 'channel' if channel_type else channel_id.startswith('C')
    if not is_public:
        logging.info(f"SKIPPED_PRIVATE_SOURCE | channel_type={channel_type or 'unknown'} | {channel_id}")
    return is_public


def _send_slack_alert(say, channel_id: str, ts: str, certainty: int):
    url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
    try:
        say(channel=Config.ALERT_CHANNEL, text=f":cake-radar: *<{url}|Cake detected!>* ({certainty}% certainty)")
    except Exception as error:
        logging.error(f"Error sending message to {Config.ALERT_CHANNEL}: {error}")


def evaluate_message(original_text: str, channel_id: str, ts: str, files: list, say, user_id: str = '', is_edit: bool = False):
    """Run all Cake Radar checks and send an alert only when warranted."""
    matched_keywords = find_cake_words(original_text)
    if not matched_keywords:
        return False
    image_data_uris = download_slack_images(files)
    result = assess_certainty(original_text.lower(), image_data_uris)
    decision = result['decision']
    total_certainty = result['total_certainty']
    reason = result.get('reason', '')
    classifier_forwarded = decision == 'yes' and total_certainty > Config.CERTAINTY_THRESHOLD
    judge_verdict = judge_reason = None
    judge_votes = []
    if classifier_forwarded:
        judge = judge_decision(original_text, reason, image_data_uris)
        judge_verdict = judge['verdict']
        judge_reason = judge['reason']
        judge_votes = judge.get('votes', [])
    forwarded = classifier_forwarded and judge_verdict != 'overturn'
    judge_part = f" | judge_panel={judge_verdict}" if judge_verdict else ''
    if judge_votes:
        judge_part += f" | judge_votes=[{ai_classifier.format_judge_votes(judge_votes)}]"
    elif judge_reason:
        judge_part += f" | judge_reason={judge_reason}"
    label = 'EVALUATED (edit)' if is_edit else 'EVALUATED'
    action = 'FORWARDED' if forwarded else 'NOT_FORWARDED'
    reason_part = f" | reason={reason}" if reason else ''
    logging.info(
        f"{label} | {action} | AI={decision} {total_certainty}%{reason_part}{judge_part} | "
        f"keywords={matched_keywords} | {_fmt_ts(ts)} | {_channel_name(channel_id)} | "
        f'{_user_name(user_id)} | "{" ".join(original_text.split())}"'
    )
    evaluated_messages[(channel_id, ts)] = set(matched_keywords)
    if forwarded:
        _send_slack_alert(say, channel_id, ts, total_certainty)
    return forwarded


def _process_event(payload: Dict, message: Dict, say, is_edit: bool):
    channel_id = payload.get('channel', '')
    ts = _canonical_changed_message_ts(payload) if is_edit else message.get('ts', '')
    text = message.get('text', '')
    thread_ts = message.get('thread_ts')
    if thread_ts and thread_ts != ts:
        return
    if channel_id == Config.CAKE_RADAR_CHANNEL_ID or not _is_public_source_channel(payload, channel_id):
        return
    if not _claim_message_evaluation(channel_id, ts, text, is_edit):
        return
    forwarded = False
    try:
        forwarded = evaluate_message(text, channel_id, ts, message.get('files', []), say, message.get('user', ''), is_edit)
    finally:
        _complete_message_evaluation(channel_id, ts, text, forwarded)


def handle_message(message, say):
    processed_messages.append((message['channel'], message['ts']))
    _process_event(message, message, say, is_edit=False)


def handle_message_events(event, say):
    if event.get('subtype') == 'message_changed':
        _process_event(event, event.get('message', {}), say, is_edit=True)
