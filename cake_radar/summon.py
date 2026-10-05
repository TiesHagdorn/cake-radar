"""Let colleagues tag Cake Radar on a treat it missed, and cross-post it if it isn't on the alert channel yet."""

from collections import deque
from datetime import datetime
import logging
import re
from threading import Lock
import time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from . import message_processor as mp
from .config import Config


_handled_mentions = deque(maxlen=1000)
_handled_mentions_lock = Lock()
_channel_public_cache: Dict[str, bool] = {}
_MENTION_RE = re.compile(r"<[@!#][^>]*>")


def _client():
    return mp._slack_app.client


def _claim_mention(channel_id: str, mention_ts: str) -> bool:
    """Handle each tag once, whether it arrives live or via the join catch-up."""
    key = (channel_id, mention_ts)
    with _handled_mentions_lock:
        if key in _handled_mentions:
            return False
        _handled_mentions.append(key)
        return True


def _channel_is_public(channel_id: str) -> bool:
    if channel_id not in _channel_public_cache:
        try:
            channel = _client().conversations_info(channel=channel_id)['channel']
            _channel_public_cache[channel_id] = not (
                channel.get('is_private') or channel.get('is_im') or channel.get('is_mpim')
            )
        except Exception as error:
            logging.warning(f"Could not look up channel {channel_id}, treating as private: {error}")
            return False
    return _channel_public_cache[channel_id]


def _start_of_today_ts() -> float:
    now = datetime.now(ZoneInfo("Europe/Amsterdam"))
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _is_candidate(message: Dict) -> bool:
    """Human messages with some content beyond mentions."""
    if message.get('bot_id') or message.get('user') == mp.bot_user_id():
        return False
    if message.get('subtype') not in (None, 'file_share', 'thread_broadcast'):
        return False
    remaining = _MENTION_RE.sub('', message.get('text', '')).strip()
    return bool(remaining or message.get('files'))


def _has_image(message: Dict) -> bool:
    return any(f.get('mimetype', '').startswith('image/') for f in message.get('files', []))


def collect_candidates(channel_id: str, mention_ts: str, thread_ts: str = '') -> List[Dict]:
    """Messages around a tag: the whole thread, or today's recent top-level messages including the tag."""
    if thread_ts:
        result = _client().conversations_replies(channel=channel_id, ts=thread_ts, limit=200)
    else:
        result = _client().conversations_history(
            channel=channel_id,
            latest=mention_ts,
            oldest=str(_start_of_today_ts()),
            inclusive=True,
            limit=Config.SUMMON_LOOKBACK_MESSAGES + 1,
        )
    return [m for m in result.get('messages', []) if _is_candidate(m)]


def find_already_alerted(channel_id: str, candidates: List[Dict]) -> Optional[Dict]:
    """Return a candidate that already has an alert, checking memory first and then the alert channel."""
    for m in candidates:
        if mp.was_forwarded(channel_id, m['ts']):
            return m
    if not candidates:
        return None

    needles = {f"/archives/{channel_id}/p{m['ts'].replace('.', '')}": m for m in candidates}
    oldest = min((m['ts'] for m in candidates), key=float)  # an alert can't predate its message
    cursor = None
    try:
        for _ in range(5):
            kwargs = {'channel': Config.CAKE_RADAR_CHANNEL_ID, 'oldest': oldest, 'limit': 200}
            if cursor:
                kwargs['cursor'] = cursor
            result = _client().conversations_history(**kwargs)
            for alert in result.get('messages', []):
                text = alert.get('text', '')
                for needle, m in needles.items():
                    if needle in text:
                        return m
            cursor = (result.get('response_metadata') or {}).get('next_cursor')
            if not cursor:
                break
    except Exception as error:
        logging.warning(f"Could not read alert channel history, assuming not alerted: {error}")
    return None


def _react(channel_id: str, ts: str, name: str):
    try:
        _client().reactions_add(channel=channel_id, timestamp=ts, name=name)
    except Exception as error:
        logging.warning(f"Could not add :{name}: reaction: {error}")


def run_summon(channel_id: str, mention_ts: str, thread_ts: str = '', user_id: str = '', source: str = 'mention'):
    """Scan the messages around a tag and cross-post a treat that isn't on the alert channel yet."""
    if channel_id == Config.CAKE_RADAR_CHANNEL_ID or not _claim_mention(channel_id, mention_ts):
        return

    log_prefix = f"SUMMON ({source})"
    log_suffix = f"{mp._fmt_ts(mention_ts)} | {mp._channel_name(channel_id)} | {mp._user_name(user_id)}"

    if not _channel_is_public(channel_id):
        logging.info(f"{log_prefix} | SKIPPED_PRIVATE_SOURCE | {log_suffix}")
        _react(channel_id, mention_ts, Config.SUMMON_FAILURE_REACTION)
        return

    try:
        candidates = collect_candidates(channel_id, mention_ts, thread_ts)
    except Exception as error:
        logging.error(f"{log_prefix} | ERROR | could not read messages: {error} | {log_suffix}")
        _react(channel_id, mention_ts, Config.SUMMON_FAILURE_REACTION)
        return

    already = find_already_alerted(channel_id, candidates)
    if already:
        logging.info(f"{log_prefix} | ALREADY_FORWARDED | ts={already['ts']} | {log_suffix}")
        _react(channel_id, mention_ts, Config.SUMMON_SUCCESS_REACTION)
        return

    to_evaluate = [
        m for m in candidates if mp.find_cake_words(m.get('text', '')) or _has_image(m)
    ][:Config.SUMMON_MAX_EVALUATIONS]

    best, best_result = None, None
    for m in to_evaluate:
        result = mp.assess_certainty(
            m.get('text', '').lower(),
            mp.download_slack_images(m.get('files', [])),
            extra_context=Config.SUMMON_PROMPT_HINT,
        )
        certainty = result.get('total_certainty', 0)
        if result.get('decision') == 'yes' and certainty > Config.SUMMON_CERTAINTY_THRESHOLD:
            if best_result is None or certainty > best_result['total_certainty']:
                best, best_result = m, result

    if best is None:
        logging.info(
            f"{log_prefix} | NOT_FORWARDED | candidates={len(candidates)} evaluated={len(to_evaluate)} | {log_suffix}"
        )
        _react(channel_id, mention_ts, Config.SUMMON_FAILURE_REACTION)
        return

    if not mp.claim_forward(channel_id, best['ts']):
        logging.info(f"{log_prefix} | ALREADY_FORWARDED | ts={best['ts']} | {log_suffix}")
        _react(channel_id, mention_ts, Config.SUMMON_SUCCESS_REACTION)
        return

    posted = mp._send_slack_alert(
        _client().chat_postMessage,
        channel_id,
        best['ts'],
        best_result['total_certainty'],
        thread_ts=best.get('thread_ts', ''),
        suffix=f" · summoned by {mp._user_name(user_id)}" if user_id else '',
    )
    if not posted:
        mp.release_forward(channel_id, best['ts'])
    flat_text = ' '.join(best.get('text', '').split())
    logging.info(
        f"{log_prefix} | {'FORWARDED' if posted else 'ERROR'} | AI={best_result['decision']} "
        f"{best_result['total_certainty']}% | reason={best_result.get('reason', '')} | {log_suffix} | \"{flat_text}\""
    )
    _react(channel_id, mention_ts, Config.SUMMON_SUCCESS_REACTION if posted else Config.SUMMON_FAILURE_REACTION)


def find_pending_mentions(channel_id: str) -> List[Dict]:
    """Recent tags of the bot in a channel, sent before the bot could see them."""
    bot_id = mp.bot_user_id()
    if not bot_id:
        return []
    now = time.time()
    mention_cutoff = now - Config.SUMMON_JOIN_LOOKBACK_SECONDS
    parent_cutoff = now - max(Config.SUMMON_JOIN_THREAD_PARENT_LOOKBACK_SECONDS, Config.SUMMON_JOIN_LOOKBACK_SECONDS)

    mentions = []
    result = _client().conversations_history(channel=channel_id, oldest=str(parent_cutoff), limit=200)
    for m in result.get('messages', []):
        if float(m['ts']) >= mention_cutoff and mp.mentions_bot(m.get('text', '')) and m.get('user') != bot_id:
            mentions.append({'ts': m['ts'], 'thread_ts': '', 'user': m.get('user', '')})
        if m.get('reply_count') and float(m.get('latest_reply', 0)) >= mention_cutoff:
            replies = _client().conversations_replies(channel=channel_id, ts=m['ts'], oldest=str(mention_cutoff), limit=200)
            for r in replies.get('messages', []):
                if r['ts'] == m['ts'] or float(r['ts']) < mention_cutoff:
                    continue
                if mp.mentions_bot(r.get('text', '')) and r.get('user') != bot_id:
                    mentions.append({'ts': r['ts'], 'thread_ts': m['ts'], 'user': r.get('user', '')})
    return sorted(mentions, key=lambda x: float(x['ts']))


def handle_app_mention(event, say):
    thread_ts = event.get('thread_ts', '')
    if thread_ts == event.get('ts'):
        thread_ts = ''
    run_summon(event.get('channel', ''), event.get('ts', ''), thread_ts, event.get('user', ''))


def handle_member_joined(event, say):
    """When the bot is invited, pick up tags that were sent while it wasn't in the channel."""
    channel_id = event.get('channel', '')
    if event.get('user') != mp.bot_user_id() or channel_id == Config.CAKE_RADAR_CHANNEL_ID:
        return
    try:
        mentions = find_pending_mentions(channel_id)
    except Exception as error:
        logging.error(f"Could not scan {mp._channel_name(channel_id)} for pending tags: {error}")
        return
    for mention in mentions:
        run_summon(channel_id, mention['ts'], mention['thread_ts'], mention['user'], source='join')
