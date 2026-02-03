from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import logging
import re
from typing import Optional, Tuple, Dict
import os
from collections import deque
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.StreamHandler()  # Log to console only
                    ])

# Initialize Config
if not Config.validate():
    exit(1)
Config.load_keywords()

# Track processed messages to handle Slack retries
processed_messages = deque(maxlen=1000)

# Initialize the Slack app and Flask app
app = App(token=Config.SLACK_BOT_TOKEN, signing_secret=Config.SLACK_SIGNING_SECRET)
client = OpenAI(api_key=Config.OPENAI_API_KEY)
flask_app = Flask(__name__)
flask_app.logger.disabled = True
handler = SlackRequestHandler(app)

# Suppress Flask's default HTTP access logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Function to assess certainty
def assess_certainty(message_text: str) -> Dict:
    """Assess the likelihood of the message being about offering something.
    
    Returns a dict with:
    - decision: 'yes' or 'no'
    - total_certainty: combined certainty score
    """
    decision = "no"
    total_certainty = 0
    
    try:
        response = client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": Config.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": Config.USER_PROMPT_TEMPLATE.format(message_text=message_text)
                }
            ]
        )
        assessment = response.choices[0].message.content.strip().lower()
        if ',' in assessment:
            decision_part, certainty_str = assessment.split(',')
            decision = decision_part.strip()
            total_certainty = int(certainty_str.strip().replace('%', ''))
        else:
             decision = assessment
    except Exception as e:
        logging.error(f"Error assessing certainty: {e}")
        return {'decision': 'no', 'total_certainty': 0}

    return {
        'decision': decision,
        'total_certainty': total_certainty,
    }

def send_slack_alert(say, channel_id, ts, decision, certainty, target_channel, original_text):
    """Helper to format and send the Slack alert."""
    message_url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
    certainty_info = f"Certainty: {certainty}%"
    
    if "yes" in decision:
        icon = ":green-light-blinker:"
        title = "Cake Alert!"
    else:
        icon = ":red_circle:"
        title = "False Alarm"
        
    full_message = f"{icon} *<{message_url}|{title}>* ({certainty_info})\n> {original_text}"
    
    try:
        say(channel=target_channel, text=full_message)
    except Exception as e:
        logging.error(f"Error sending message to {target_channel}: {e}")

# Listen for messages and check for keywords
@app.message()
def handle_message(message, say):
    original_text = message.get('text', '')
    text = original_text.lower()
    channel_id = message['channel']
    ts = message['ts']                          # Timestamp of the message
    thread_ts = message.get('thread_ts')        # Check if the message is a thread reply

    # Deduplicate messages to prevent handling retries
    if (channel_id, ts) in processed_messages:
        logging.info(f"Ignoring duplicate message {ts} in channel {channel_id}")
        return
    processed_messages.append((channel_id, ts))

    # Log the incoming message text
    logging.info(f"Received message: '{text}' from channel: {channel_id} (timestamp: {ts})")

    # Exclude messages in threads
    if thread_ts and thread_ts != ts:
        logging.info("Message is a thread reply and will be ignored.")
        return

    # Check if the message is from the #cake-radar channel
    if channel_id == Config.CAKE_RADAR_CHANNEL_ID:
        logging.info(f"Message from cake-radar channel ({Config.CAKE_RADAR_CHANNEL_ID}) ignored.")
        return

    # Check for keywords using regex
    if any(re.search(rf"{keyword}", text, re.IGNORECASE) for keyword in Config.KEYWORDS):
        
        # Assess the certainty of the message
        result = assess_certainty(text)
        
        decision = result['decision']
        total_certainty = result['total_certainty']

        # Log the assessment and certainty level to the terminal
        logging.info(f"Assessed Message: '{text}', Decision: {decision}, Total: {total_certainty}%")

        # Routing logic
        if decision and "yes" in decision and total_certainty > Config.CERTAINTY_THRESHOLD:
            send_slack_alert(say, channel_id, ts, decision, total_certainty, Config.ALERT_CHANNEL, original_text)
        elif decision and "no" in decision:
            send_slack_alert(say, channel_id, ts, decision, total_certainty, Config.FALSE_ALARM_CHANNEL, original_text)

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