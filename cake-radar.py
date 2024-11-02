from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import logging
import re
from typing import Optional, Tuple
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("cake-radar.log"),  # Log to a file
                        logging.StreamHandler()  # Also log to console
                    ])

# Load and validate environment variables
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Check if required environment variables are set
if not all([SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET, OPENAI_API_KEY]):
    logging.error("One or more environment variables are missing!")
    exit(1)

# Initialize the Slack app and Flask app
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# Keywords to search for in messages
KEYWORDS = ["candy", "cake", "treat", "snack", "praline"]
PLURAL_KEYWORDS = [keyword + 's' for keyword in KEYWORDS]  # Adding plurals
ALL_KEYWORDS = KEYWORDS + PLURAL_KEYWORDS

# Function to assess certainty of the message
def assess_certainty(message_text: str) -> Optional[Tuple[str, int]]:
    """Assess the likelihood of the message being about offering something."""
    try:
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": f"Respond with 'yes' or 'no' and include certainty level in percentage (0-100) for the following message: '{message_text}'"
                }
            ],
            model="gpt-3.5-turbo",
        )

        # Example response format: "yes, 85%"
        assessment = response.choices[0].message.content.strip().lower()
        if ',' in assessment:
            decision, certainty_str = assessment.split(',')
            certainty = int(certainty_str.strip().replace('%', ''))  # Convert percentage string to int
            return decision.strip(), certainty
        return assessment, 0  # Default to 0% if no certainty is provided
    except Exception as e:
        print(f"Error assessing message certainty: {e}")
        return None, 0

# Helper function to construct Slack message URL
def construct_message_url(channel_id: str, ts: str) -> str:
    return f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"

# Listen for messages and check for keywords
@app.message()
def handle_message(message, say):
    text = message.get('text', '').lower()
    channel_id = message['channel']
    ts = message['ts']  # Timestamp of the message

    # Check for keywords using regex
    if any(re.search(rf"\b{keyword}\b", text) for keyword in ALL_KEYWORDS):
        # Assess the certainty of the message
        assessment, certainty = assess_certainty(text)

        # Log the assessment and certainty level to the terminal
        logging.info(f"Assessed Message: '{text}', Assessment: {assessment}, Certainty: {certainty}%")

        # Only cross-post if the assessment is 'yes' and certainty is over 75%
        if assessment and "yes" in assessment and certainty > 74:
            # Construct the message URL
            message_url = construct_message_url(channel_id, ts)

            # Create the full message with certainty percentage
            full_message = f":green-light-blinker: *<{message_url}|Cake Alert!>* (Certainty: {certainty}%)"

            # Cross-post the message URL to #241017-incident-store-cake
            try:
                say(channel="#241017-incident-store-cake", text=full_message)
            except Exception as e:
                print(f"Error sending message to channel: {e}")

# URL Verification route
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

# Start the Flask app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))  # Default to 3000 if PORT is not set
    flask_app.run(host='0.0.0.0', port=port)