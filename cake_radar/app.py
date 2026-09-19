"""Start Cake Radar and pass Slack events to the message processor."""

import logging

from flask import Flask, request
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

from . import message_processor
from .config import Config


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
        logging.basicConfig(level=logging.INFO, format='%(levelname)-7s %(message)s')
    logging.getLogger('werkzeug').setLevel(logging.CRITICAL)
    logging.getLogger('gunicorn.access').setLevel(logging.WARNING)
    logging.getLogger('gunicorn.error').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    _logging_configured = True


configure_logging()


def initialize(slack_app=None, openai_client=None, validate_config=True):
    """Initialize external clients and connect Slack to Cake Radar."""
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
    message_processor.configure(app, client)
    register_handlers(app)
    return flask_app


def ensure_initialized():
    if app is None or client is None or handler is None:
        initialize()
    return app, client, handler


def register_handlers(slack_app):
    slack_app.message()(message_processor.handle_message)
    slack_app.event("message")(message_processor.handle_message_events)


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    if request.headers.get("X-Slack-Retry-Num"):
        return "", 200
    _, _, slack_handler = ensure_initialized()
    return slack_handler.handle(request)


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
        result = message_processor.assess_message(text)
        if not result['matched_keywords']:
            print("❌ No cake keywords found.")
            return
        print(f"✅ Keywords found: {result['matched_keywords']}")
        print("🤔 Assessing certainty with AI...")
        print("\n--- Classifier Result ---")
        print(f"Decision: {result['decision'].upper()}")
        print(f"Total Certainty: {result['total_certainty']}%")
        print(f"Reason: {result.get('reason', '')}")
        if result['classifier_forwarded']:
            print("\n⚖️  Classifier said yes + above threshold — running judge...")
            print("\n--- Judge Result ---")
            print(f"Verdict: {result['judge']['verdict'].upper()}")
            print(f"Reason: {result['judge']['reason']}")
            print("\n--- Final ---")
            print(f"{'✅ FORWARD' if result['forwarded'] else '🚫 SUPPRESS'}")
        else:
            print("\n(Classifier below threshold — judge not run.)")

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
