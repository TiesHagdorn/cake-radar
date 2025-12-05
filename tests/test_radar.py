import pytest
import re
import sys
import os
import json
from unittest.mock import MagicMock, patch

# Add the project root to sys.path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the code to be tested
# Since the code is not in a module yet, we import from cake-radar.py
# This is a bit hacky because the file has a hyphen. We use __import__
cake_radar = __import__('cake-radar')
# Register it in sys.modules so @patch('cake_radar.client') works
sys.modules['cake_radar'] = cake_radar

def test_keywords_loaded():
    """Verify keywords are loaded correctly."""
    assert len(cake_radar.KEYWORDS) > 0
    assert "cake" in cake_radar.KEYWORDS
    assert "croissant" in cake_radar.KEYWORDS

def test_keyword_matching():
    """Verify regex matching works for various cake phrases."""
    cases = [
        ("I brought cake", True),
        ("There is cake in the kitchen", True),
        ("Anyone want a croissant?", True),
        ("I hate mondays", False),
        ("The project is a piece of cake", True), # Technically a match, though false positive context
        ("Let's have a meeting", False),
        ("pancake", True), # Matches 'cake' inside
    ]
    
    for text, expected in cases:
        found = any(re.search(rf"{keyword}", text, re.IGNORECASE) for keyword in cake_radar.KEYWORDS)
        assert found == expected, f"Failed for text: '{text}'"

@patch('cake_radar.client')
def test_assess_certainty_positive(mock_client):
    """Verify assess_certainty handles positive AI response."""
    # Mock the OpenAI response
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Yes, 95%"
    mock_client.chat.completions.create.return_value = mock_response

    result = cake_radar.assess_certainty("There is cake")
    
    assert result['decision'] == 'yes'
    assert result['total_certainty'] == 95
    assert result['text_certainty'] == 95

@patch('cake_radar.client')
def test_assess_certainty_negative(mock_client):
    """Verify assess_certainty handles negative AI response."""
    # Mock the OpenAI response
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "No, 10%"
    mock_client.chat.completions.create.return_value = mock_response

    result = cake_radar.assess_certainty("Meeting time")
    
    assert 'no' in result['decision']
    # Start of Selection
    assert result['total_certainty'] == 10

@patch('cake_radar.client') # Corrected: mock 'cake_radar.client' instead of 'cake_radar.ai.client' as it's a global in the script
def test_assess_certainty_garbage_response(mock_client):
    """Verify code handles garbage unexpected response gracefully."""
    # Mock the OpenAI response to be weird
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "I like turtles"
    mock_client.chat.completions.create.return_value = mock_response

    result = cake_radar.assess_certainty("Weird text")
    
    # Should probably default to 0 or hande it without crashing
    assert result['total_certainty'] == 0
