from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List
from collections import deque
from . import classifier
from .config import Config
from .images import download_slack_images as _download_slack_images
from .matching import match_keywords

# Track processed messages to handle Slack retries
processed_messages = deque(maxlen=1000)

# Track evaluated messages: (channel_id, ts) -> set of matched keywords (used to suppress duplicate edit logs)
evaluated_messages = {}

# Channel name cache: id -> "#name"
_channel_name_cache: Dict[str, str] = {}


class SlackEventsAccessLogFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        return not (
            '"POST /slack/events HTTP/' in message
            and '" 200 ' in message
            and '"Slackbot 1.0 ' in message
        )


def _install_access_log_filters():
    for logger_name in ('gunicorn.access', 'werkzeug'):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(log_filter, SlackEventsAccessLogFilter) for log_filter in logger.filters):
            logger.addFilter(SlackEventsAccessLogFilter())


def _channel_name(channel_id: str) -> str:
    if channel_id not in _channel_name_cache:
        try:
            slack_app, _, _ = ensure_initialized()
            result = slack_app.client.conversations_info(channel=channel_id)
            _channel_name_cache[channel_id] = '#' + result['channel']['name']
        except Exception:
            _channel_name_cache[channel_id] = channel_id
    return _channel_name_cache[channel_id]

_user_name_cache: Dict[str, str] = {}

def _user_name(user_id: str) -> str:
    if user_id not in _user_name_cache:
        try:
            slack_app, _, _ = ensure_initialized()
            result = slack_app.client.users_info(user=user_id)
            profile = result['user']['profile']
            name = profile.get('display_name') or profile.get('real_name') or user_id
            _user_name_cache[user_id] = '@' + name
        except Exception:
            _user_name_cache[user_id] = '@' + user_id
    return _user_name_cache[user_id]

def _fmt_ts(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=ZoneInfo("Europe/Amsterdam")).strftime("%H:%M")
    except Exception:
        return ts


def _canonical_changed_message_ts(event: Dict) -> str:
    """Return the Slack message ts for an edit event, not the edit event ts."""
    previous = event.get('previous_message') or {}
    updated = event.get('message') or {}
    return previous.get('ts') or updated.get('ts', '')

flask_app = Flask(__name__)
flask_app.logger.disabled = True
app = None
client = None
handler = None
_logging_configured = False

def configure_logging():
    global _logging_configured

    _install_access_log_filters()
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(logging.INFO)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(levelname)-7s %(message)s',
            handlers=[logging.StreamHandler()],
        )
    logging.getLogger('werkzeug').setLevel(logging.CRITICAL)
    logging.getLogger('gunicorn.access').setLevel(logging.WARNING)
    logging.getLogger('gunicorn.error').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    _logging_configured = True

configure_logging()

def initialize(slack_app=None, openai_client=None, validate_config=True):
    """Initialize external clients and register Slack handlers."""
    global app, client, handler

    if validate_config and not Config.validate():
        raise RuntimeError("One or more environment variables are missing")

    Config.load_keywords()
    app = slack_app or App(
        token=Config.SLACK_BOT_TOKEN,
        signing_secret=Config.SLACK_SIGNING_SECRET,
        token_verification_enabled=Config.SLACK_TOKEN_VERIFICATION_ENABLED,
    )
    client = openai_client or OpenAI(api_key=Config.OPENAI_API_KEY)
    handler = SlackRequestHandler(app)
    register_handlers(app)
    return flask_app

def ensure_initialized():
    if app is None or client is None or handler is None:
        initialize()
    return app, client, handler

def register_handlers(slack_app):
    slack_app.message()(handle_message)
    slack_app.event("message")(handle_message_events)

def _openai_operational_error_kind(error: Exception) -> str:
    return classifier.openai_operational_error_kind(error)

def notify_openai_operational_error(error: Exception, context: str):
    """Post a Slack alert for OpenAI configuration problems."""
    kind = _openai_operational_error_kind(error)
    if not kind:
        return

    if kind == 'auth':
        detail = "OpenAI authentication failed."
    else:
        detail = "OpenAI quota or billing failed."

    target_channel = Config.OPERATIONAL_ALERT_CHANNEL
    if not target_channel:
        logging.error("OpenAI operational alert suppressed: no OPERATIONAL_ALERT_CHANNEL configured")
        return

    text = (
        f"Hi {Config.OPERATIONAL_ALERT_SUPPORT_MENTION}, I'm broken, please check the logs!\n"
        f"{detail} Treat alerts may be missed until this is fixed. Context: `{context}`."
    )

    try:
        slack_app, _, _ = ensure_initialized()
        slack_app.client.chat_postMessage(channel=target_channel, text=text)
    except Exception as slack_error:
        logging.error(f"Failed to send operational alert: {slack_error}")

def download_slack_images(files: list, max_images: int = 1) -> List[str]:
    return _download_slack_images(files, Config.SLACK_BOT_TOKEN, max_images)

# Function to assess certainty
def assess_certainty(message_text: str, image_data_uris: List[str] = None) -> Dict:
    _, openai_client, _ = ensure_initialized()
    return classifier.assess_certainty(
        openai_client,
        message_text,
        notify_openai_operational_error,
        image_data_uris,
    )

def _parse_judge_response(raw_response: str) -> Dict:
    return classifier.parse_judge_response(raw_response)

def judge_decision(message_text: str, classifier_reason: str, image_data_uris: List[str] = None) -> Dict:
    _, openai_client, _ = ensure_initialized()
    return classifier.judge_decision(
        openai_client,
        message_text,
        classifier_reason,
        notify_openai_operational_error,
        image_data_uris,
    )

def _format_judge_votes(votes: List[Dict]) -> str:
    return classifier.format_judge_votes(votes)


def send_slack_alert(say, channel_id, ts, certainty, target_channel):
    """Helper to format and send the Slack alert."""
    message_url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
    certainty_info = f"{certainty}% certainty"

    icon = ":cake-radar:"
    title = "Cake detected!"
    full_message = f"{icon} *<{message_url}|{title}>* ({certainty_info})"
    
    try:
        say(channel=target_channel, text=full_message)
    except Exception as e:
        logging.error(f"Error sending message to {target_channel}: {e}")


def _is_public_source_channel(payload: Dict, channel_id: str) -> bool:
    channel_type = payload.get('channel_type') or payload.get('message', {}).get('channel_type')
    if channel_type:
        is_public = channel_type == 'channel'
    else:
        is_public = channel_id.startswith('C')

    if not is_public:
        logging.info(f"SKIPPED_PRIVATE_SOURCE | channel_type={channel_type or 'unknown'} | {channel_id}")

    return is_public


def evaluate_message(original_text: str, channel_id: str, ts: str, files: list, say, user_id: str = '', is_edit: bool = False):
    """Run keyword matching, AI evaluation, logging, and forwarding for a message."""
    text = original_text.lower()

    matched_keywords = match_keywords(text)
    if not matched_keywords:
        return

    image_data_uris = download_slack_images(files)
    result = assess_certainty(text, image_data_uris)

    decision = result['decision']
    total_certainty = result['total_certainty']
    reason = result.get('reason', '')

    classifier_forwarded = decision == "yes" and total_certainty > Config.CERTAINTY_THRESHOLD

    judge_verdict = None
    judge_reason = None
    judge_votes = []
    if classifier_forwarded:
        judge = judge_decision(original_text, reason, image_data_uris)
        judge_verdict = judge['verdict']
        judge_reason = judge['reason']
        judge_votes = judge.get('votes', [])

    forwarded = classifier_forwarded and judge_verdict != 'overturn'
    action = "FORWARDED" if forwarded else "NOT_FORWARDED"
    label = "EVALUATED (edit)" if is_edit else "EVALUATED"

    flat_text = ' '.join(original_text.split())
    reason_part = f" | reason={reason}" if reason else ""
    judge_part = ""
    if judge_verdict:
        judge_part = f" | judge_panel={judge_verdict}"
        if judge_votes:
            judge_part += f" | judge_votes=[{_format_judge_votes(judge_votes)}]"
        elif judge_reason:
            judge_part += f" | judge_reason={judge_reason}"
    logging.info(
        f"{label} | {action} | AI={decision} {total_certainty}%{reason_part}{judge_part} | "
        f"keywords={matched_keywords} | {_fmt_ts(ts)} | {_channel_name(channel_id)} | "
        f'{_user_name(user_id)} | "{flat_text}"'
    )

    evaluated_messages[(channel_id, ts)] = set(matched_keywords)

    if forwarded:
        send_slack_alert(say, channel_id, ts, total_certainty, Config.ALERT_CHANNEL)


def handle_message(message, say):
    original_text = message.get('text', '')
    channel_id = message['channel']
    ts = message['ts']
    thread_ts = message.get('thread_ts')
    user_id = message.get('user', '')

    # Deduplicate messages to prevent handling retries
    if (channel_id, ts) in processed_messages:
        return
    processed_messages.append((channel_id, ts))

    # Exclude thread replies
    if thread_ts and thread_ts != ts:
        return

    # Exclude messages from #cake-radar itself
    if channel_id == Config.CAKE_RADAR_CHANNEL_ID:
        return

    # Only forward messages from public channels. Private channels, DMs, and group DMs
    # may contain sensitive context and should never be reposted to #cake-radar.
    if not _is_public_source_channel(message, channel_id):
        return

    evaluate_message(original_text, channel_id, ts, message.get('files', []), say, user_id=user_id)


def handle_message_events(event, say):
    subtype = event.get('subtype')

    if subtype == 'message_changed':
        updated = event.get('message', {})
        original_text = updated.get('text', '')
        channel_id = event.get('channel', '')
        ts = _canonical_changed_message_ts(event)
        user_id = updated.get('user', '')

        # Remove old dedup entry so the edited version is evaluated fresh
        key = (channel_id, ts)
        if key in processed_messages:
            processed_messages.remove(key)
        processed_messages.append(key)

        if channel_id == Config.CAKE_RADAR_CHANNEL_ID:
            return

        # Only forward messages from public channels. Private channels, DMs, and group DMs
        # may contain sensitive context and should never be reposted to #cake-radar.
        if not _is_public_source_channel(event, channel_id):
            return

        # Exclude thread replies
        thread_ts = updated.get('thread_ts')
        if thread_ts and thread_ts != ts:
            return

        # If already evaluated, only re-evaluate if the edit introduces new cake keywords
        if key in evaluated_messages:
            text_lower = original_text.lower()
            new_keywords = set(match_keywords(text_lower))
            if not new_keywords - evaluated_messages[key]:
                return

        evaluate_message(original_text, channel_id, ts, updated.get('files', []), say, user_id=user_id, is_edit=True)

# URL Verification route
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    if request.headers.get("X-Slack-Retry-Num"):
        return "", 200
    _, _, slack_handler = ensure_initialized()
    return slack_handler.handle(request)

# Start the Flask app or run in CLI mode
def main():
    import argparse
    import sys

    configure_logging()
    parser = argparse.ArgumentParser(description="Cake Radar Bot")
    parser.add_argument("--test", type=str, help="Test a single message string")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    args = parser.parse_args()

    try:
        initialize()
    except RuntimeError as exc:
        logging.error(str(exc))
        sys.exit(1)

    def print_assessment(text):
        print(f"\n--- Testing Message: '{text}' ---")
        found_keywords = match_keywords(text)

        if found_keywords:
            print(f"✅ Keywords found: {found_keywords}")
            print("🤔 Assessing certainty with AI...")
            result = assess_certainty(text)

            print(f"\n--- Classifier Result ---")
            print(f"Decision: {result['decision'].upper()}")
            print(f"Total Certainty: {result['total_certainty']}%")
            print(f"Reason: {result.get('reason', '')}")

            classifier_forwarded = (
                result['decision'] == "yes"
                and result['total_certainty'] > Config.CERTAINTY_THRESHOLD
            )
            if classifier_forwarded:
                print("\n⚖️  Classifier said yes + above threshold — running judge...")
                judge = judge_decision(text, result.get('reason', ''))
                print(f"\n--- Judge Result ---")
                print(f"Verdict: {judge['verdict'].upper()}")
                print(f"Reason: {judge['reason']}")
                final = classifier_forwarded and judge['verdict'] != 'overturn'
                print(f"\n--- Final ---")
                print(f"{'✅ FORWARD' if final else '🚫 SUPPRESS'}")
            else:
                print("\n(Classifier below threshold — judge not run.)")
        else:
            print("❌ No cake keywords found.")

    if args.test:
        print_assessment(args.test)
        sys.exit(0)

    if args.interactive:
        print("🍰 Cake Radar Interactive Mode")
        print("Type a message to test (or 'exit'/'quit' to stop):")
        while True:
            try:
                user_input = input("\n> ")
                if user_input.lower() in ['exit', 'quit']:
                    break
                if not user_input.strip():
                    continue
                print_assessment(user_input)
            except KeyboardInterrupt:
                break
        print("\nBye! 👋")
        sys.exit(0)

    flask_app.run(host='0.0.0.0', port=Config.PORT)


if __name__ == "__main__":
    main()
