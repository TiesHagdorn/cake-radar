import os
from unittest.mock import MagicMock, patch

os.environ.setdefault('SLACK_BOT_TOKEN', 'xoxb-dummy')
os.environ.setdefault('SLACK_SIGNING_SECRET', 'dummy')
os.environ.setdefault('OPENAI_API_KEY', 'dummy')
os.environ.setdefault('SLACK_TOKEN_VERIFICATION_ENABLED', 'false')

from cake_radar import app as cake_radar

def _decorator(*args, **kwargs):
    def wrapper(func):
        return func
    return wrapper

fake_slack_app = MagicMock()
fake_slack_app.message.side_effect = _decorator
fake_slack_app.event.side_effect = _decorator
cake_radar.initialize(
    slack_app=fake_slack_app,
    openai_client=MagicMock(),
    validate_config=False,
)

def test_keywords_loaded():
    """Verify keywords are loaded correctly."""
    assert len(cake_radar.Config.KEYWORDS) > 0
    assert "cake" in cake_radar.Config.KEYWORDS
    assert "croissant" in cake_radar.Config.KEYWORDS

def test_keyword_matching():
    """Verify regex matching works for various cake phrases."""
    cases = [
        ("I brought cake", True),
        ("There is cake in the kitchen", True),
        ("Anyone want a croissant?", True),
        ("I hate mondays", False),
        ("The project is a piece of cake", True),
        ("Let's have a meeting", False),
        ("pancake", True),
    ]
    
    for text, expected in cases:
        found = bool(cake_radar.match_keywords(text))
        assert found == expected, f"Failed for text: '{text}'"

@patch('cake_radar.app.client')
def test_assess_certainty_positive(mock_client):
    """Verify assess_certainty handles positive AI response."""
    # Mock the OpenAI response
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Yes, 95%"
    mock_client.chat.completions.create.return_value = mock_response

    result = cake_radar.assess_certainty("There is cake")
    
    assert result['decision'] == 'yes'
    assert result['total_certainty'] == 95

@patch('cake_radar.app.client')
def test_assess_certainty_negative(mock_client):
    """Verify assess_certainty handles negative AI response."""
    # Mock the OpenAI response
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "No, 10%"
    mock_client.chat.completions.create.return_value = mock_response

    result = cake_radar.assess_certainty("Meeting time")
    
    assert 'no' in result['decision']
    assert result['total_certainty'] == 10

@patch('cake_radar.app.client')
def test_assess_certainty_garbage_response(mock_client):
    """Verify code handles garbage unexpected response gracefully."""
    # Mock the OpenAI response to be weird
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "I like turtles"
    mock_client.chat.completions.create.return_value = mock_response

    result = cake_radar.assess_certainty("Weird text")
    
    assert result['total_certainty'] == 0
