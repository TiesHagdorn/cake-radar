import unittest
import os
from unittest.mock import MagicMock, patch

os.environ['SLACK_BOT_TOKEN'] = 'xoxb-dummy'
os.environ['SLACK_SIGNING_SECRET'] = 'dummy'
os.environ['OPENAI_API_KEY'] = 'dummy'
os.environ['SLACK_TOKEN_VERIFICATION_ENABLED'] = 'false'

from cake_radar import app as cake_radar

def _decorator(*args, **kwargs):
    def wrapper(func):
        return func
    return wrapper

def _fake_slack_app():
    slack_app = MagicMock()
    slack_app.message.side_effect = _decorator
    slack_app.event.side_effect = _decorator
    return slack_app

class TestDeduplication(unittest.TestCase):

    def setUp(self):
        """Clear state before each test."""
        cake_radar.processed_messages.clear()
        cake_radar.evaluated_messages.clear()
        cake_radar.initialize(
            slack_app=_fake_slack_app(),
            openai_client=MagicMock(),
            validate_config=False,
        )
        cake_radar.Config.OPERATIONAL_ALERT_CHANNEL = 'COPS'
        cake_radar.Config.OPERATIONAL_ALERT_SUPPORT_MENTION = '@support'
        cake_radar.app.client.chat_postMessage.reset_mock()

    def tearDown(self):
        """Clear state after each test."""
        cake_radar.processed_messages.clear()
        cake_radar.evaluated_messages.clear()

    @patch('cake_radar.app.assess_certainty')
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

    @patch('cake_radar.app.assess_certainty')
    def test_edit_no_new_keywords_not_reforwarded(self, mock_assess):
        """Edited message already forwarded with same keywords should not be forwarded again."""
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 90, 'prompt_tokens': 10, 'completion_tokens': 5}

        # Original message is forwarded
        msg = {'text': 'cake in the kitchen', 'channel': 'C1', 'ts': '1000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)
        self.assertIn(('C1', '1000.00'), cake_radar.evaluated_messages)

        # Edit the message — same keywords, just minor rewording
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'there is cake in the kitchen!', 'ts': '1000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        # Should NOT have been re-evaluated
        self.assertEqual(mock_assess.call_count, 1)

    @patch('cake_radar.app.assess_certainty')
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

    @patch('cake_radar.app.assess_certainty')
    def test_edit_not_previously_forwarded_is_evaluated(self, mock_assess):
        """Edited message that was never forwarded should be evaluated normally."""
        mock_say = MagicMock()
        # First evaluation returns no — message not forwarded
        mock_assess.return_value = {'decision': 'no', 'total_certainty': 40, 'prompt_tokens': 10, 'completion_tokens': 5}

        msg = {'text': 'cake?', 'channel': 'C1', 'ts': '3000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)
        self.assertIn(('C1', '3000.00'), cake_radar.evaluated_messages)

        # Edit with same keywords — should be suppressed
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'cake is in the kitchen!', 'ts': '3000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)
        self.assertEqual(mock_assess.call_count, 1)

    @patch('cake_radar.app.assess_certainty')
    def test_edit_not_forwarded_new_keyword_triggers_reevaluation(self, mock_assess):
        """Edited non-forwarded message with a new keyword should be re-evaluated."""
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'no', 'total_certainty': 40, 'prompt_tokens': 10, 'completion_tokens': 5}

        msg = {'text': 'cake?', 'channel': 'C1', 'ts': '3000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)

        # Edit adds a new keyword — should be re-evaluated
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 92, 'prompt_tokens': 10, 'completion_tokens': 5}
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'cake and baklava in the kitchen!', 'ts': '3000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)
        self.assertEqual(mock_assess.call_count, 2)

    @patch('cake_radar.app.assess_certainty')
    def test_thread_replies_ignored(self, mock_assess):
        """Verify that thread replies are ignored."""
        mock_say = MagicMock()
        
        # Thread reply message
        msg = {'text': 'cake', 'channel': 'C1', 'ts': '1000.00', 'thread_ts': '999.00'}
        cake_radar.handle_message(msg, mock_say)
        
        # Should NOT be processed
        self.assertEqual(mock_assess.call_count, 0)

    def test_openai_auth_error_sends_operational_alert(self):
        """OpenAI auth failures should alert in the test/support channel."""
        error = Exception("Error code: 401 - {'error': {'code': 'invalid_api_key'}}")

        cake_radar.notify_openai_operational_error(error, 'classifier')
        cake_radar.notify_openai_operational_error(error, 'classifier')

        self.assertEqual(cake_radar.app.client.chat_postMessage.call_count, 2)
        for call in cake_radar.app.client.chat_postMessage.call_args_list:
            kwargs = call.kwargs
            self.assertEqual(kwargs['channel'], 'COPS')
            self.assertNotIn('thread_ts', kwargs)
            self.assertIn('@support', kwargs['text'])
            self.assertIn("I'm broken, please check the logs", kwargs['text'])

    def test_judge_policy_allows_unlabeled_shared_location_food(self):
        """Judge instructions should not require an explicit offer for office treat sightings."""
        judges = cake_radar.Config.JUDGE_SYSTEM_PROMPTS
        prompts = {judge['name']: judge['prompt'].lower() for judge in judges}
        combined_prompt = ' '.join(prompts.values())

        self.assertEqual(
            [judge['name'] for judge in judges],
            ['availability', 'false_positive', 'social_context'],
        )
        self.assertEqual(combined_prompt.count("clear reason to veto"), 3)
        self.assertIn("focus on availability", prompts['availability'])
        self.assertIn("shared amsterdam office location", prompts['availability'])
        self.assertIn("focus on known false positives", prompts['false_positive'])
        self.assertIn("otherwise uphold", prompts['false_positive'])
        self.assertIn("focus on social intent", prompts['social_context'])
        self.assertIn("informal sightings", prompts['social_context'])
        for category in (
            "future event",
            "non-food item",
            "out-of-scope location",
            "private/personal food",
            "idiom/metaphor",
            "birthday/congrats",
        ):
            self.assertIn(category, combined_prompt)

    @patch('cake_radar.app.client')
    def test_judge_panel_requires_two_overturns_to_suppress(self, mock_client):
        """One dissenting judge should not suppress an otherwise valid alert."""
        responses = []
        for content in (
            '{"verdict": "uphold", "reason": "current food in office"}',
            '{"verdict": "overturn", "reason": "no explicit offer"}',
            '{"verdict": "uphold", "reason": "reporting shared food"}',
        ):
            response = MagicMock()
            response.choices[0].message.content = content
            responses.append(response)
        mock_client.chat.completions.create.side_effect = responses

        result = cake_radar.judge_decision("Hi, a cake :birthday: no name at the entrance!", "cake offered")

        self.assertEqual(result['verdict'], 'uphold')
        self.assertEqual(len(result['votes']), 3)
        self.assertEqual(mock_client.chat.completions.create.call_count, 3)
        for call in mock_client.chat.completions.create.call_args_list:
            self.assertEqual(call.kwargs['response_format'], {"type": "json_object"})

    @patch('cake_radar.app.client')
    def test_judge_panel_majority_overturn_suppresses(self, mock_client):
        """Two overturn votes should suppress a classifier yes."""
        responses = []
        for content in (
            '{"verdict": "overturn", "reason": "future event"}',
            '{"verdict": "overturn", "reason": "party invite"}',
            '{"verdict": "uphold", "reason": "mentions food"}',
        ):
            response = MagicMock()
            response.choices[0].message.content = content
            responses.append(response)
        mock_client.chat.completions.create.side_effect = responses

        result = cake_radar.judge_decision("Cake next Friday at the party", "cake mentioned")

        self.assertEqual(result['verdict'], 'overturn')
        self.assertEqual(len(result['votes']), 3)

    def test_format_judge_votes_includes_each_outcome_and_reason(self):
        votes = [
            {'name': 'availability', 'verdict': 'uphold', 'reason': 'available now'},
            {'name': 'false_positive', 'verdict': 'overturn', 'reason': 'future event'},
            {'name': 'social_context', 'verdict': 'uphold', 'reason': 'informal sighting'},
        ]

        formatted = cake_radar._format_judge_votes(votes)

        self.assertEqual(
            formatted,
            "availability=uphold (available now); "
            "false_positive=overturn (future event); "
            "social_context=uphold (informal sighting)",
        )

    @patch('cake_radar.app.judge_decision')
    @patch('cake_radar.app.assess_certainty')
    def test_evaluation_log_includes_each_judge_vote(self, mock_assess, mock_judge):
        mock_say = MagicMock()
        mock_assess.return_value = {
            'decision': 'yes',
            'total_certainty': 97,
            'reason': 'cake offered at entrance',
            'prompt_tokens': 10,
            'completion_tokens': 5,
        }
        mock_judge.return_value = {
            'verdict': 'uphold',
            'reason': 'panel summary',
            'votes': [
                {'name': 'availability', 'verdict': 'uphold', 'reason': 'available now'},
                {'name': 'false_positive', 'verdict': 'overturn', 'reason': 'no explicit offer'},
                {'name': 'social_context', 'verdict': 'uphold', 'reason': 'informal sighting'},
            ],
        }

        with self.assertLogs(level='INFO') as logs:
            cake_radar.evaluate_message(
                "Hi, a cake :birthday: no name at the entrance!",
                "C1",
                "1782909778.761469",
                [],
                mock_say,
                user_id="U1",
            )

        log_output = '\n'.join(logs.output)
        self.assertIn("judge_panel=uphold", log_output)
        self.assertIn("availability=uphold (available now)", log_output)
        self.assertIn("false_positive=overturn (no explicit offer)", log_output)
        self.assertIn("social_context=uphold (informal sighting)", log_output)

if __name__ == '__main__':
    unittest.main()
