from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import logging
import re
from typing import Optional, Tuple, Dict
from dotenv import load_dotenv
import os
import requests
import base64

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.StreamHandler()  # Log to console only
                    ])

# Load and validate environment variables
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Channel configuration
CAKE_RADAR_CHANNEL_ID = os.getenv("CAKE_RADAR_CHANNEL_ID", "C07RTPCLAKC")  # Channel to ignore messages from
ALERT_CHANNEL = os.getenv("ALERT_CHANNEL", "#cake-radar")  # Channel for positive alerts
FALSE_ALARM_CHANNEL = os.getenv("FALSE_ALARM_CHANNEL", "#241126-cake-radar-false-alarms")  # Channel for false alarms

# Check if required environment variables are set
if not all([SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET, OPENAI_API_KEY]):
    logging.error("One or more environment variables are missing!")
    exit(1)

# Initialize the Slack app and Flask app
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
flask_app = Flask(__name__)
flask_app.logger.disabled = True
handler = SlackRequestHandler(app)

# Suppress Flask's default HTTP access logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Keywords to search for in messages
# Load keywords from JSON file
import json
with open('keywords.json', 'r') as f:
    KEYWORDS = json.load(f)

PLURAL_KEYWORDS = [keyword + 's' for keyword in KEYWORDS]  # Adding plurals
ALL_KEYWORDS = KEYWORDS + PLURAL_KEYWORDS

# Function to get image URLs from Slack files and convert to base64
def get_image_data(files: list) -> list:
    """Download images from Slack and convert to base64 data URLs.
    
    Slack's private URLs require authentication, so we download them
    using the bot token and convert to base64 for OpenAI.
    """
    image_data_urls = []
    for file in files:
        if file.get('mimetype', '').startswith('image/'):
            url = file.get('url_private_download') or file.get('url_private')
            if url:
                try:
                    # Download image with Slack bot token authentication
                    headers = {'Authorization': f'Bearer {SLACK_BOT_TOKEN}'}
                    response = requests.get(url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        # Convert to base64
                        image_base64 = base64.b64encode(response.content).decode('utf-8')
                        mime_type = file.get('mimetype', 'image/jpeg')
                        data_url = f"data:{mime_type};base64,{image_base64}"
                        image_data_urls.append(data_url)
                        logging.info(f"Successfully downloaded and encoded image: {file.get('name', 'unknown')}")
                    else:
                        logging.error(f"Failed to download image from {url}: HTTP {response.status_code}")
                except Exception as e:
                    logging.error(f"Error downloading image from {url}: {e}")
    return image_data_urls

# Function to assess text certainty
def assess_text_certainty(message_text: str) -> Tuple[str, int]:
    """Assess the likelihood of the message text being about offering something."""
    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that evaluates whether a Slack message is about offering an edible treat. Respond with 'yes' or 'no' and include certainty level in percentage (0-100%). Example: 'Yes, 95%' or 'No, 80%'."},
                {
                    "role": "user",
                    "content": f"Only respond with 'yes' or 'no' and include certainty level in percentage (0%-100%) that represents how likely you are that the message is about a colleague offering an edible treat (like a cake, candy, or pie). If the message mentions a location or hub outside of Amsterdam, be more confident in 'no'. If the message contains a lot of other information about work, be more confident in your 'no'. Example response format is: 'Yes, 95%' or 'No, 80%'. Message: '{message_text}'"
                }
            ]
        )
        assessment = response.choices[0].message.content.strip().lower()
        if ',' in assessment:
            decision, certainty_str = assessment.split(',')
            certainty = int(certainty_str.strip().replace('%', ''))
            return decision.strip(), certainty
        return assessment, 0
    except Exception as e:
        logging.error(f"Error assessing text certainty: {e}")
        return None, 0

# Function to assess image certainty
def assess_image_certainty(image_data_urls: list) -> Tuple[str, int]:
    """Assess the likelihood of images showing food/treats."""
    try:
        user_content = [{"type": "text", "text": "Do these images show edible treats like cake, candy, pie, or snacks? Respond with 'yes' or 'no' and include certainty level in percentage (0-100%). Example: 'Yes, 95%' or 'No, 80%'."}]
        
        for image_data_url in image_data_urls:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": image_data_url,
                    "detail": "low"
                }
            })
        
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that analyzes images to detect food and treats. Respond with 'yes' or 'no' and include certainty level in percentage (0-100%)."},
                {"role": "user", "content": user_content}
            ]
        )
        
        assessment = response.choices[0].message.content.strip().lower()
        if ',' in assessment:
            decision, certainty_str = assessment.split(',')
            certainty = int(certainty_str.strip().replace('%', ''))
            return decision.strip(), certainty
        return assessment, 0
    except Exception as e:
        logging.error(f"Error assessing image certainty: {e}")
        return None, 0

# Function to assess certainty with detailed breakdown
def assess_certainty(message_text: str, image_data_urls: list = None) -> Dict:
    """Assess the likelihood of the message being about offering something.
    
    Returns a dict with:
    - decision: 'yes' or 'no'
    - total_certainty: combined certainty score
    - text_certainty: certainty from text analysis
    - image_certainty: certainty from image analysis (None if no images)
    """
    # Assess text
    text_decision, text_certainty = assess_text_certainty(message_text)
    
    # Assess images if available
    image_decision, image_certainty = None, None
    if image_data_urls:
        logging.info(f"Analyzing {len(image_data_urls)} image(s)")
        image_decision, image_certainty = assess_image_certainty(image_data_urls)
    
    # Combine decisions and certainties
    if image_data_urls and image_decision:
        # Both text and image available - use weighted average
        # Give more weight to images (60%) as they're more reliable
        total_certainty = int(text_certainty * 0.4 + image_certainty * 0.6)
        # Decision is 'yes' if either is 'yes' with high confidence
        if 'yes' in text_decision or 'yes' in image_decision:
            decision = 'yes'
        else:
            decision = 'no'
    else:
        # Only text available
        decision = text_decision
        total_certainty = text_certainty
    
    return {
        'decision': decision,
        'total_certainty': total_certainty,
        'text_certainty': text_certainty,
        'image_certainty': image_certainty
    }

# Listen for messages and check for keywords
@app.message()
def handle_message(message, say):
    text = message.get('text', '').lower()
    channel_id = message['channel']
    ts = message['ts']                          # Timestamp of the message
    thread_ts = message.get('thread_ts')        # Check if the message is a thread reply
    files = message.get('files', [])            # Get attached files

    # Log the incoming message text
    logging.info(f"Received message: '{text}' from channel: {channel_id} (timestamp: {ts})")

    # Exclude messages in threads
    if thread_ts and thread_ts != ts:
        logging.info("Message is a thread reply and will be ignored.")
        return

    # Check if the message is from the #cake-radar channel
    if channel_id == CAKE_RADAR_CHANNEL_ID:
        logging.info(f"Message from cake-radar channel ({CAKE_RADAR_CHANNEL_ID}) ignored.")
        return

    # Check for keywords using regex
    if any(re.search(rf"{keyword}", text, re.IGNORECASE) for keyword in KEYWORDS):
        # Get image data (base64 encoded) if any images are attached
        image_data_urls = get_image_data(files)
        
        # Assess the certainty of the message (with images if available)
        result = assess_certainty(text, image_data_urls)
        
        decision = result['decision']
        total_certainty = result['total_certainty']
        text_certainty = result['text_certainty']
        image_certainty = result['image_certainty']

        # Log the assessment and certainty level to the terminal
        logging.info(f"Assessed Message: '{text}', Decision: {decision}, Total: {total_certainty}%, Text: {text_certainty}%, Image: {image_certainty}%")

        # Only cross-post if the assessment is 'yes' and certainty is high.
        if decision and "yes" in decision and total_certainty > 85:
            # Construct the message URL to crosspost
            message_url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"

            # Create the full message with detailed certainty breakdown
            if image_certainty is not None:
                certainty_info = f"Total certainty: {total_certainty}%. Message certainty: {text_certainty}%, Image certainty: {image_certainty}%"
            else:
                certainty_info = f"Certainty: {total_certainty}%"
            
            full_message = f":green-light-blinker: *<{message_url}|Cake Alert!>* ({certainty_info})"

            # Cross-post the message to alert channel
            try:
                say(channel=ALERT_CHANNEL, text=full_message)
            except Exception as e:
                logging.error(f"Error sending message to {ALERT_CHANNEL}: {e}")
        elif decision and "no" in decision:
            # Send negative assessments to false alarm channel
            message_url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
            
            # Create the full message with detailed certainty breakdown
            if image_certainty is not None:
                certainty_info = f"Total certainty: {total_certainty}%. Message certainty: {text_certainty}%, Image certainty: {image_certainty}%"
            else:
                certainty_info = f"Certainty: {total_certainty}%"
            
            full_message = f":red_circle: *<{message_url}|False Alarm>* ({certainty_info})"
            
            # Cross-post the message to false alarm channel
            try:
                say(channel=FALSE_ALARM_CHANNEL, text=full_message)
            except Exception as e:
                print(f"Error sending message to {FALSE_ALARM_CHANNEL}: {e}")

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
        found_keywords = [k for k in KEYWORDS if re.search(rf"{k}", text, re.IGNORECASE)]
        
        if found_keywords:
            print(f"✅ Keywords found: {found_keywords}")
            print("🤔 Assessing certainty with AI...")
            result = assess_certainty(text)
            
            print(f"\n--- Assessment Result ---")
            print(f"Decision: {result['decision'].upper()}")
            print(f"Total Certainty: {result['total_certainty']}%")
            print(f"Text Certainty: {result['text_certainty']}%")
            if result['image_certainty'] is not None:
                print(f"Image Certainty: {result['image_certainty']}%")
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

    port = int(os.environ.get("PORT", 3000))  # Default to 3000 if PORT is not set
    flask_app.run(host='0.0.0.0', port=port)