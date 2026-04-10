import os
import json
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Central configuration for the Cake Radar bot."""
    # Secrets
    SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
    SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
    SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # Channels
    CAKE_RADAR_CHANNEL_ID = os.getenv("CAKE_RADAR_CHANNEL_ID", "C07RTPCLAKC")
    ALERT_CHANNEL = os.getenv("ALERT_CHANNEL", "#cake-radar")

    # AI Settings
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
    CERTAINTY_THRESHOLD = int(os.getenv("CERTAINTY_THRESHOLD", "85"))
    INPUT_COST_PER_MTOK = float(os.getenv("INPUT_COST_PER_MTOK", "0.20"))
    OUTPUT_COST_PER_MTOK = float(os.getenv("OUTPUT_COST_PER_MTOK", "1.25"))

    # Daily summary
    SUMMARY_USER_ID = os.getenv("SUMMARY_USER_ID")
    SUMMARY_HOUR = int(os.getenv("SUMMARY_HOUR", "17"))

    # App Settings
    PORT = int(os.getenv("PORT", 3000))
    
    SYSTEM_PROMPT = "You are a helpful assistant that evaluates whether a Slack message is about offering an edible treat. You may receive a text message, an image, or both. Respond with 'yes' or 'no' and include certainty level in percentage (0-100%). Example: 'Yes, 95%' or 'No, 80%'."
    USER_PROMPT_TEMPLATE = "Only respond with 'yes' or 'no' and include certainty level in percentage (0%-100%) that represents how likely you are that the message is about a colleague offering an edible treat (like a cake, candy, or pie). If the message mentions a location or hub outside of Amsterdam, be more confident in 'no'. If the message contains a lot of other information about work, be more confident in your 'no'. If an image is attached and it clearly shows an edible treat, increase your confidence in 'yes'. Example response format is: 'Yes, 95%' or 'No, 80%'. Message: '{message_text}'"

    # Keywords
    KEYWORDS = []
    
    @classmethod
    def load_keywords(cls):
        try:
            with open('keywords.json', 'r') as f:
                base_keywords = json.load(f)
            plural_keywords = [k + 's' for k in base_keywords]
            cls.KEYWORDS = base_keywords + plural_keywords
        except Exception as e:
            logging.error(f"Failed to load keywords: {e}")
            cls.KEYWORDS = ['cake', 'donuts'] # Fallback

    @classmethod
    def validate(cls):
        if not all([cls.SLACK_BOT_TOKEN, cls.SLACK_APP_TOKEN, cls.SLACK_SIGNING_SECRET, cls.OPENAI_API_KEY]):
            logging.error("One or more environment variables are missing!")
            return False
        return True
