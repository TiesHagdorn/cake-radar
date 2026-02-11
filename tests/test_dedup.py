import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import cake-radar
# Mock dependencies if they don't exist
from unittest.mock import MagicMock
sys.modules['slack_bolt'] = MagicMock()
sys.modules['slack_bolt.adapter.flask'] = MagicMock()
sys.modules['flask'] = MagicMock()
sys.modules['openai'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

# Setup App mock to ignore decorators
def message_decorator(*args, **kwargs):
    def decorator(func):
        return func
    return decorator

# Configure the mock App class to return an instance whose .message() method returns the decorator
mock_app_instance = MagicMock()
mock_app_instance.message.side_effect = message_decorator
sys.modules['slack_bolt'].App.return_value = mock_app_instance

# Set dummy env vars to pass Config.validate()
os.environ['SLACK_BOT_TOKEN'] = 'xoxb-dummy'
os.environ['SLACK_APP_TOKEN'] = 'xapp-dummy'
os.environ['SLACK_SIGNING_SECRET'] = 'dummy'
os.environ['OPENAI_API_KEY'] = 'dummy'

cake_radar = __import__('cake-radar')
sys.modules['cake_radar'] = cake_radar

class TestDeduplication(unittest.TestCase):

    def setUp(self):
        """Clear processed_messages before each test."""
        cake_radar.processed_messages.clear()

    def tearDown(self):
        """Clear processed_messages after each test."""
        cake_radar.processed_messages.clear()

    @patch('cake_radar.assess_certainty')
    def test_deduplication_logic(self, mock_assess):
        """Verify that messages with same channel_id and ts are ignored."""
        # Setup mock
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'no', 'total_certainty': 0}
        
        # Message 1
        msg1 = {'text': 'cake', 'channel': 'C1', 'ts': '1000.00'}
        cake_radar.handle_message(msg1, mock_say)
        
        # Should have been processed
        self.assertEqual(mock_assess.call_count, 1)
        
        # Message 1 Retry (Same ID)
        cake_radar.handle_message(msg1, mock_say)
        
        # Should NOT have been processed again
        self.assertEqual(mock_assess.call_count, 1)
        
        # Message 2 (Same text, different channel = different message)
        msg2 = {'text': 'cake', 'channel': 'C2', 'ts': '1000.00'} 
        cake_radar.handle_message(msg2, mock_say)
        
        # Should have been processed
        self.assertEqual(mock_assess.call_count, 2)

    @patch('cake_radar.assess_certainty')
    def test_thread_replies_ignored(self, mock_assess):
        """Verify that thread replies are ignored."""
        mock_say = MagicMock()
        
        # Thread reply message
        msg = {'text': 'cake', 'channel': 'C1', 'ts': '1000.00', 'thread_ts': '999.00'}
        cake_radar.handle_message(msg, mock_say)
        
        # Should NOT be processed
        self.assertEqual(mock_assess.call_count, 0)

if __name__ == '__main__':
    unittest.main()

