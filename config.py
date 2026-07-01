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
    SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # Channels
    CAKE_RADAR_CHANNEL_ID = os.getenv("CAKE_RADAR_CHANNEL_ID", "C07RTPCLAKC")
    ALERT_CHANNEL = os.getenv("ALERT_CHANNEL", "#cake-radar")
    OPERATIONAL_ALERT_CHANNEL = os.getenv("OPERATIONAL_ALERT_CHANNEL", "C07SBFP9GDR")
    OPERATIONAL_ALERT_SUPPORT_MENTION = os.getenv("OPERATIONAL_ALERT_SUPPORT_MENTION", "<!subteam^S0ACA5Y6W48>")

    # AI Settings
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
    JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-5.4")
    CERTAINTY_THRESHOLD = int(os.getenv("CERTAINTY_THRESHOLD", "85"))

    # App Settings
    PORT = int(os.getenv("PORT", 3000))

    SYSTEM_PROMPT = "You are a helpful assistant that evaluates whether a Slack message is about offering an edible treat that is currently available or being offered imminently (e.g. 'I brought cake', 'there are snacks in the kitchen'). Do NOT classify as yes if the message is about a future event, party invitation, or calendar announcement, even if food will be present. You may receive a text message, an image, or both. Respond with 'yes' or 'no', a certainty level in percentage (0-100%), and a brief reason. Example: 'Yes, 95%, cake visible in photo' or 'No, 80%, future event invitation'."
    USER_PROMPT_TEMPLATE = "Only respond with 'yes' or 'no', a certainty level in percentage (0%-100%), and a brief reason phrase explaining your classification. The offered item MUST be edible food or a drink — non-food items such as books, merchandise, swag, stickers, or any physical item that cannot be eaten do not qualify, even if they are free or described as a treat. If the message mentions a location or hub outside of Amsterdam, be more confident in 'no'. If the message is primarily about work topics and only tangentially mentions food (e.g. a meeting agenda that includes lunch), be more confident in 'no'. However, if the message clearly offers or announces available treats — even alongside work context like a milestone celebration — classify based on the treat offering. If the message is directed at someone else (e.g. wishing them happy birthday, congratulating them), it is not a treat offering — be very confident in 'no'. Only say 'yes' when the author themselves is offering or announcing available food. If an image is attached and it clearly shows an edible treat, increase your confidence in 'yes'. Example response format is: 'Yes, 95%, cake on desk in photo' or 'No, 80%, future party invitation'. Message: '{message_text}'"

    JUDGE_SYSTEM_PROMPTS = [
        {
            "name": "availability",
            "prompt": (
                "Cake Radar alerts colleagues when edible food or drink is likely available to share "
                "in the Amsterdam office. The classifier already found this message likely relevant. "
                "Your role is only to decide whether there is a clear reason to veto the alert. "
                "Focus on availability. Uphold if the message suggests edible food or drink is "
                "available now or imminently in a shared Amsterdam office location. Overturn only if "
                "it is clearly future, already gone, outside Amsterdam, or private/personal."
            ),
        },
        {
            "name": "false_positive",
            "prompt": (
                "Cake Radar alerts colleagues when edible food or drink is likely available to share "
                "in the Amsterdam office. The classifier already found this message likely relevant. "
                "Your role is only to decide whether there is a clear reason to veto the alert. "
                "Focus on known false positives. Overturn only if the message is clearly about a "
                "non-food item, an idiom/metaphor, a future event, an out-of-scope location, "
                "private/personal food, or a birthday/congrats message with no available food. "
                "Otherwise uphold."
            ),
        },
        {
            "name": "social_context",
            "prompt": (
                "Cake Radar alerts colleagues when edible food or drink is likely available to share "
                "in the Amsterdam office. The classifier already found this message likely relevant. "
                "Your role is only to decide whether there is a clear reason to veto the alert. "
                "Focus on social intent. Uphold if the message reads like someone is alerting "
                "colleagues to shared office food, including informal sightings. Overturn only if it "
                "reads merely as a joke, personal wish, work agenda mention, or private meal."
            ),
        },
    ]
    JUDGE_USER_PROMPT_TEMPLATE = (
        "The classifier said YES with reason: '{classifier_reason}'.\n\n"
        "Message: '{message_text}'\n\n"
        "Respond in this exact format: 'uphold, <brief reason>' or 'overturn, <brief reason>'."
    )

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
        if not all([cls.SLACK_BOT_TOKEN, cls.SLACK_SIGNING_SECRET, cls.OPENAI_API_KEY]):
            logging.error("One or more environment variables are missing!")
            return False
        return True
