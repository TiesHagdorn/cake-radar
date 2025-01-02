from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import logging
import os
import requests
from dotenv import load_dotenv
from typing import Optional, Tuple

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
flask_app.logger.disabled = True
handler = SlackRequestHandler(app)

# Suppress Flask's default HTTP access logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Keywords to search for in messages
KEYWORDS = [
    "anniversary", "appelgebak", "appeltaart", "arnhemse meisjes", "baklava", "babka", 
    "banana bread", "beignet", "birthday", "biscuit", "black forest", "blondie", 
    "bonbon", "brandy snap", "brownie", "buche de noel", "cake", "cannoli", 
    "caramel", "carrot cake", "celebration", "cheesecake", "chocolade", "chocolate", 
    "chocolate bar", "churros", "clafoutis", "cookie", "crumble", "croissant", 
    "croquembouche", "cupcake", "danish", "djupur", "donut", "duivekater", 
    "easter egg", "eclair", "fritter", "fudge", "galette", "gateau", "gelato",
    "gingerbread", "gummy bear", "honeycomb", "jellybean", "kitkat", "koulouri", 
    "krentenbrood", "krentenbol", "kruidkoek", "lemon drizzle", "liquorice", 
    "macaron", "malteser", "marsepein", "marzipan", "meringue", "mochi", 
    "muffin", "nougat", "oliebol", "oliebollen", "ontbijtkoek", "panettone", "parfait", 
    "pastry", "pancake", "pavlova", "pie", "poffertjes", "pudding", 
    "profiterole", "praline", "red velvet", "rocky road", "roomboterkoek", 
    "roombroodje", "rosette", "scone", "shortbread", "soufflé", "spekulaas", 
    "speculaas", "sponge cake", "stroopwafel", "strudel", "sundae", "sweetbread", 
    "syrup cake", "tart", "tiramisu", "toffee", "tompouce", "torte", "brought some", "kitchen area",
    "truffle", "vacation", "vlaai", "waffle", "worstenbrood", "zeppole"
    "cake", "treat", "cookie", "brownie", "snack", "pie", "muffin", "dessert", "sweets",
    "pudding", "chocolate", "anniversary", "birthday", "celebration"
]

# Function to assess certainty of the text and image in context
def assess_text_and_image_in_context(message_text: str, message_image_url: Optional[str]) -> Tuple[int, int, bool]:
    text_certainty = 0
    image_certainty = 0
    cross_post = False

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            messages=[
                {
                    "role": "system", 
                    "content": "You are a helpful assistant that evaluates whether a Slack message sent in a public Slack channel is about offering an edible treat, such as cake or snacks. Respond with 'yes' or 'no' and include certainty level in percentage (0-100%) for the following message."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Only respond with 'yes' or 'no' and include certainty level in percentage (0%-100%) that represents how likely you are that the message is, or is not, about a colleague offering an edible treat (like a cake, candy, or pie). As I'd only want to look for edible treats in the office, if the message mentions a location or hub outside of Amsterdam, be more confident in 'no'. If the message contains a lot of other information about work, but not about the treat, also be more confident in your 'no'. Example response format: 'yes, message certainty is 85%, image certainty is 60%'. This is the message to assess: '{message_text}'"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": message_image_url,
                                "detail": "low",
                            }
                        }
                    ]
                }
            ]
        )


        # Parse the response (assuming OpenAI returns both text and image certainties)
        assessment = response.choices[0].message.content.strip().lower()
        if ',' in assessment:
            text_certainty_str, image_certainty_str = assessment.split(',')
            text_certainty = int(text_certainty_str.replace('%', '').strip())
            image_certainty = int(image_certainty_str.replace('%', '').strip())
        else:
            text_certainty = 0
            image_certainty = 0

        logging.info(f"Text Certainty: {text_certainty}%, Image Certainty: {image_certainty}%")

        # Cross-post if either certainty is 85% or higher
        if text_certainty >= 85 or image_certainty >= 85:
            cross_post = True

    except Exception as e:
        logging.error(f"Error assessing text and image: {e}")

    return text_certainty, image_certainty, cross_post

# Message handler
@app.message()
def handle_message(message, say):
    text = message.get('text', '').lower()
    files = message.get('files', [])

    message_image_url = None
    for file in files:
        if file.get("mimetype", "").startswith("image/"):
            message_image_url = file["url_private"]
            break


    # Assess the message and image in context
    text_certainty, image_certainty, should_cross_post = assess_text_and_image_in_context(text, image_data)

    # Log certainty values
    logging.info(
        f"Text Certainty: {text_certainty}%, Image Certainty: {image_certainty}%, Cross-Post: {should_cross_post}"
    )

    # Cross-post if justified
    if should_cross_post:
        message_url = f"https://slack.com/archives/{message['channel']}/p{message['ts'].replace('.', '')}"
        full_message = (
            f":green-light-blinker: *<{message_url}|Cake Alert!>*\n"
            f"- Text Certainty: {text_certainty}%\n"
            f"- Image Certainty: {image_certainty}%"
        )
        say(channel="#241126-incident-cake", text=full_message)

# URL Verification route
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

# Start the Flask app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))  # Default to 3000 if PORT is not set
    flask_app.run(host='0.0.0.0', port=port)
