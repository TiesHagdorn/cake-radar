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
    FALSE_ALARM_CHANNEL = os.getenv("FALSE_ALARM_CHANNEL", "#241126-cake-radar-false-alarms")

    # AI Settings
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
    CERTAINTY_THRESHOLD = int(os.getenv("CERTAINTY_THRESHOLD", "85"))

    # App Settings
    PORT = int(os.getenv("PORT", 3000))
    
    SYSTEM_PROMPT = "You are a helpful assistant that evaluates whether a Slack message is about offering an edible treat. Respond with 'yes' or 'no' and include certainty level in percentage (0-100%). Example: 'Yes, 95%' or 'No, 80%'."
    USER_PROMPT_TEMPLATE = "Only respond with 'yes' or 'no' and include certainty level in percentage (0%-100%) that represents how likely you are that the message is about a colleague offering an edible treat (like a cake, candy, or pie). If the message mentions a location or hub outside of Amsterdam, be more confident in 'no'. If the message contains a lot of other information about work, be more confident in your 'no'. Example response format is: 'Yes, 95%' or 'No, 80%'. Message: '{message_text}'"

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
