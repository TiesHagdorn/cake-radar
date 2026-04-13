from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import logging
import re
import base64
import io
import requests
from PIL import Image
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict, List
import os
from collections import deque
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(levelname)-7s %(message)s',
                    handlers=[logging.StreamHandler()])

# Initialize Config
if not Config.validate():
    exit(1)
Config.load_keywords()

# Track processed messages to handle Slack retries
processed_messages = deque(maxlen=1000)

# Track forwarded messages: (channel_id, ts) -> set of matched keywords
forwarded_messages = {}

# Channel name cache: id -> "#name"
_channel_name_cache: Dict[str, str] = {}

def _channel_name(channel_id: str) -> str:
    if channel_id not in _channel_name_cache:
        try:
            result = app.client.conversations_info(channel=channel_id)
            _channel_name_cache[channel_id] = '#' + result['channel']['name']
        except Exception:
            _channel_name_cache[channel_id] = channel_id
    return _channel_name_cache[channel_id]

_user_name_cache: Dict[str, str] = {}

def _user_name(user_id: str) -> str:
    if user_id not in _user_name_cache:
        try:
            result = app.client.users_info(user=user_id)
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

# Daily stats accumulator
daily_stats = {
    'messages_evaluated': 0,
    'messages_forwarded': 0,
    'total_prompt_tokens': 0,
    'total_completion_tokens': 0,
    'total_cost': 0.0,
}

# Initialize the Slack app and Flask app
app = App(token=Config.SLACK_BOT_TOKEN, signing_secret=Config.SLACK_SIGNING_SECRET)
client = OpenAI(api_key=Config.OPENAI_API_KEY)
flask_app = Flask(__name__)
flask_app.logger.disabled = True
handler = SlackRequestHandler(app)

# Suppress noisy third-party loggers
logging.getLogger('werkzeug').setLevel(logging.CRITICAL)
logging.getLogger('gunicorn.access').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

OPENAI_SUPPORTED_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}

def download_slack_images(files: list, max_images: int = 1) -> List[str]:
    """Download image attachments from a Slack message and return as base64 data URIs."""
    data_uris = []
    for f in files:
        if len(data_uris) >= max_images:
            break
        mimetype = f.get('mimetype', '')
        url = f.get('url_private')
        if not mimetype.startswith('image/') or not url:
            continue
        try:
            response = requests.get(url, headers={'Authorization': f'Bearer {Config.SLACK_BOT_TOKEN}'}, timeout=10)
            response.raise_for_status()
            if mimetype in OPENAI_SUPPORTED_IMAGE_TYPES:
                encoded = base64.b64encode(response.content).decode('utf-8')
                data_uris.append(f"data:{mimetype};base64,{encoded}")
            else:
                try:
                    img = Image.open(io.BytesIO(response.content)).convert('RGB')
                    buf = io.BytesIO()
                    img.save(buf, 'JPEG')
                    encoded = base64.b64encode(buf.getvalue()).decode('utf-8')
                    data_uris.append(f"data:image/jpeg;base64,{encoded}")
                    logging.warning(f"Converted {mimetype} image to JPEG for OpenAI compatibility")
                except Exception as conv_err:
                    logging.warning(f"Could not convert {mimetype} image, skipping: {conv_err}")
        except Exception as e:
            logging.error(f"Failed to download Slack image: {e}")
    return data_uris

def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate the cost of an API call in dollars."""
    return (prompt_tokens * Config.INPUT_COST_PER_MTOK + completion_tokens * Config.OUTPUT_COST_PER_MTOK) / 1_000_000

# Function to assess certainty
def assess_certainty(message_text: str, image_data_uris: List[str] = None) -> Dict:
    """Assess the likelihood of the message being about offering something.

    Returns a dict with:
    - decision: 'yes' or 'no'
    - total_certainty: combined certainty score
    - prompt_tokens: number of input tokens used
    - completion_tokens: number of output tokens used
    """
    decision = "no"
    total_certainty = 0
    prompt_text = Config.USER_PROMPT_TEMPLATE.format(message_text=message_text)

    if image_data_uris:
        user_content = [{"type": "text", "text": prompt_text}]
        for uri in image_data_uris:
            user_content.append({"type": "image_url", "image_url": {"url": uri, "detail": "low"}})
    else:
        user_content = prompt_text

    def _call_openai(content):
        return client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": Config.SYSTEM_PROMPT},
                {"role": "user", "content": content}
            ]
        )

    try:
        response = _call_openai(user_content)
    except Exception as e:
        if image_data_uris:
            logging.warning(f"OpenAI image error, retrying without images: {e}")
            try:
                response = _call_openai(prompt_text)
            except Exception as e2:
                logging.error(f"Error assessing certainty: {e2}")
                return {'decision': 'no', 'total_certainty': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
        else:
            logging.error(f"Error assessing certainty: {e}")
            return {'decision': 'no', 'total_certainty': 0, 'prompt_tokens': 0, 'completion_tokens': 0}

    try:
        assessment = response.choices[0].message.content.strip().lower()
        if ',' in assessment:
            decision_part, certainty_str = assessment.split(',')
            decision = decision_part.strip()
            total_certainty = int(certainty_str.strip().replace('%', ''))
        else:
             decision = assessment
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens
    except Exception as e:
        logging.error(f"Error parsing OpenAI response: {e}")
        return {'decision': 'no', 'total_certainty': 0, 'prompt_tokens': 0, 'completion_tokens': 0}

    return {
        'decision': decision,
        'total_certainty': total_certainty,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
    }

def send_slack_alert(say, channel_id, ts, decision, certainty, target_channel, original_text):
    """Helper to format and send the Slack alert."""
    message_url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
    certainty_info = f"Certainty: {certainty}%"
    
    icon = ":green-light-blinker:"
    title = "Cake Alert!"
    full_message = f"{icon} *<{message_url}|{title}>* ({certainty_info})"
    
    try:
        say(channel=target_channel, text=full_message)
    except Exception as e:
        logging.error(f"Error sending message to {target_channel}: {e}")

def evaluate_message(original_text: str, channel_id: str, ts: str, files: list, say, user_id: str = '', is_edit: bool = False):
    """Run keyword matching, AI evaluation, logging, and forwarding for a message."""
    text = original_text.lower()

    matched_keywords = [k for k in Config.KEYWORDS if re.search(rf"{k}", text, re.IGNORECASE)]
    if not matched_keywords:
        return

    image_data_uris = download_slack_images(files)
    result = assess_certainty(text, image_data_uris)

    decision = result['decision']
    total_certainty = result['total_certainty']
    prompt_tokens = result['prompt_tokens']
    completion_tokens = result['completion_tokens']
    cost = calculate_cost(prompt_tokens, completion_tokens)

    forwarded = decision and "yes" in decision and total_certainty > Config.CERTAINTY_THRESHOLD
    action = "FORWARDED" if forwarded else "NOT_FORWARDED"
    label = "EVALUATED (edit)" if is_edit else "EVALUATED"

    logging.info(
        f"{label} | {_channel_name(channel_id)} | {_fmt_ts(ts)} | {_user_name(user_id)} | "
        f'"{original_text}" | keywords={matched_keywords} | '
        f"AI={decision} {total_certainty}% | {action}"
    )

    daily_stats['messages_evaluated'] += 1
    daily_stats['total_prompt_tokens'] += prompt_tokens
    daily_stats['total_completion_tokens'] += completion_tokens
    daily_stats['total_cost'] += cost

    if forwarded:
        daily_stats['messages_forwarded'] += 1
        forwarded_messages[(channel_id, ts)] = set(matched_keywords)
        send_slack_alert(say, channel_id, ts, decision, total_certainty, Config.ALERT_CHANNEL, original_text)


def send_daily_summary():
    """Send a daily summary to the summary channel and reset stats."""
    try:
        date_str = datetime.now(ZoneInfo("Europe/Amsterdam")).strftime("%-d %b %Y")
        text = (
            f"*Cake Radar \u2014 Daily Summary ({date_str})*\n"
            f"Messages evaluated: {daily_stats['messages_evaluated']}\n"
            f"Forwarded to #cake-radar: {daily_stats['messages_forwarded']}\n"
            f"Total tokens: {daily_stats['total_prompt_tokens'] + daily_stats['total_completion_tokens']:,} "
            f"({daily_stats['total_prompt_tokens']:,} in + {daily_stats['total_completion_tokens']:,} out)\n"
            f"Estimated cost: ${daily_stats['total_cost']:.4f}"
        )
        app.client.chat_postMessage(channel=Config.SUMMARY_CHANNEL_ID, text=text)
        logging.info(f"Daily summary sent to {Config.SUMMARY_CHANNEL_ID}")
    except Exception as e:
        logging.error(f"Failed to send daily summary: {e}")
    finally:
        for key in daily_stats:
            daily_stats[key] = 0 if isinstance(daily_stats[key], int) else 0.0


def _daily_summary_loop():
    """Background thread: sleep until SUMMARY_TIME Amsterdam time, send summary, repeat."""
    tz = ZoneInfo("Europe/Amsterdam")
    hour, minute = (int(x) for x in Config.SUMMARY_TIME.split(':'))
    while True:
        now = datetime.now(tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        logging.info(f"Daily summary scheduled in {sleep_seconds/3600:.1f}h (at {target.strftime('%H:%M %Z')})")
        threading.Event().wait(sleep_seconds)
        send_daily_summary()


# Start daily summary background thread
threading.Thread(target=_daily_summary_loop, daemon=True).start()

# Listen for new messages
@app.message()
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

    evaluate_message(original_text, channel_id, ts, message.get('files', []), say, user_id=user_id)


# Listen for edited messages
@app.event("message")
def handle_message_events(event, say):
    subtype = event.get('subtype')

    if subtype == 'message_changed':
        updated = event.get('message', {})
        original_text = updated.get('text', '')
        channel_id = event.get('channel', '')
        ts = updated.get('ts', '')
        user_id = updated.get('user', '')

        # Remove old dedup entry so the edited version is evaluated fresh
        key = (channel_id, ts)
        if key in processed_messages:
            processed_messages.remove(key)
        processed_messages.append(key)

        if channel_id == Config.CAKE_RADAR_CHANNEL_ID:
            return

        # Exclude thread replies
        thread_ts = updated.get('thread_ts')
        if thread_ts and thread_ts != ts:
            return

        # If already forwarded, only re-evaluate if the edit introduces new cake keywords
        if key in forwarded_messages:
            text_lower = original_text.lower()
            new_keywords = {k for k in Config.KEYWORDS if re.search(rf"{k}", text_lower, re.IGNORECASE)}
            if not new_keywords - forwarded_messages[key]:
                return

        evaluate_message(original_text, channel_id, ts, updated.get('files', []), say, user_id=user_id, is_edit=True)

# URL Verification route
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

# Start the Flask app
# Start the Flask app or run in CLI mode
if __name__ == "__main__":
    import argparse
    import sys

    def print_assessment(text):
        print(f"\n--- Testing Message: '{text}' ---")
        found_keywords = [k for k in Config.KEYWORDS if re.search(rf"{k}", text, re.IGNORECASE)]
        
        if found_keywords:
            print(f"✅ Keywords found: {found_keywords}")
            print("🤔 Assessing certainty with AI...")
            result = assess_certainty(text)
            
            print(f"\n--- Assessment Result ---")
            print(f"Decision: {result['decision'].upper()}")
            print(f"Total Certainty: {result['total_certainty']}%")
        else:
            print("❌ No cake keywords found.")

    parser = argparse.ArgumentParser(description="Cake Radar Bot")
    parser.add_argument("--test", type=str, help="Test a single message string")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    args = parser.parse_args()

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