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
mock_app_instance.event.side_effect = message_decorator
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
        """Clear state before each test."""
        cake_radar.processed_messages.clear()
        cake_radar.forwarded_messages.clear()

    def tearDown(self):
        """Clear state after each test."""
        cake_radar.processed_messages.clear()
        cake_radar.forwarded_messages.clear()

    @patch('cake_radar.assess_certainty')
    def test_deduplication_logic(self, mock_assess):
        """Verify that messages with same channel_id and ts are ignored."""
        # Setup mock
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'no', 'total_certainty': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
        
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
    def test_edit_no_new_keywords_not_reforwarded(self, mock_assess):
        """Edited message already forwarded with same keywords should not be forwarded again."""
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 90, 'prompt_tokens': 10, 'completion_tokens': 5}

        # Original message is forwarded
        msg = {'text': 'cake in the kitchen', 'channel': 'C1', 'ts': '1000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)
        self.assertIn(('C1', '1000.00'), cake_radar.forwarded_messages)

        # Edit the message — same keywords, just minor rewording
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'there is cake in the kitchen!', 'ts': '1000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        # Should NOT have been re-evaluated
        self.assertEqual(mock_assess.call_count, 1)

    @patch('cake_radar.assess_certainty')
    def test_edit_new_keyword_triggers_reevaluation(self, mock_assess):
        """Edited message with a brand-new cake keyword should be re-evaluated."""
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 90, 'prompt_tokens': 10, 'completion_tokens': 5}

        # Original message forwarded with keyword 'cake'
        msg = {'text': 'cake in the office', 'channel': 'C1', 'ts': '2000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)

        # Edit adds a new keyword (e.g. 'baklava') not in the original
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'cake and baklava in the office', 'ts': '2000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        # Should have been re-evaluated because of new keyword
        self.assertEqual(mock_assess.call_count, 2)

    @patch('cake_radar.assess_certainty')
    def test_edit_not_previously_forwarded_is_evaluated(self, mock_assess):
        """Edited message that was never forwarded should be evaluated normally."""
        mock_say = MagicMock()
        # First evaluation returns no — message not forwarded
        mock_assess.return_value = {'decision': 'no', 'total_certainty': 40, 'prompt_tokens': 10, 'completion_tokens': 5}

        msg = {'text': 'cake?', 'channel': 'C1', 'ts': '3000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)
        self.assertNotIn(('C1', '3000.00'), cake_radar.forwarded_messages)

        # Now edit it — should be re-evaluated since it was never forwarded
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 92, 'prompt_tokens': 10, 'completion_tokens': 5}
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'cake is in the kitchen!', 'ts': '3000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)
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

