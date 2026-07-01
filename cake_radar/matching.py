import re
from typing import List

from .config import Config


def match_keywords(text: str) -> List[str]:
    """Return configured keywords that appear as standalone terms."""
    text = text.lower()
    return [
        keyword
        for keyword in Config.KEYWORDS
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.IGNORECASE)
    ]

