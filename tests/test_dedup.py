import unittest
import os
import threading
from unittest.mock import MagicMock, patch

os.environ['SLACK_BOT_TOKEN'] = 'xoxb-dummy'
os.environ['SLACK_SIGNING_SECRET'] = 'dummy'
os.environ['OPENAI_API_KEY'] = 'dummy'
os.environ['SLACK_TOKEN_VERIFICATION_ENABLED'] = 'false'

from cake_radar import message_processor as cake_radar

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
        cake_radar._message_states.clear()
        cake_radar.configure(_fake_slack_app(), MagicMock())
        cake_radar.Config.OPERATIONAL_ALERT_CHANNEL = 'COPS'
        cake_radar.Config.OPERATIONAL_ALERT_SUPPORT_MENTION = '@support'
        cake_radar.Config.CAKE_RADAR_CHANNEL_ID = 'C07RTPCLAKC'
        cake_radar._slack_app.client.chat_postMessage.reset_mock()

    def tearDown(self):
        """Clear state after each test."""
        cake_radar._message_states.clear()

    @patch('cake_radar.message_processor.assess_certainty')
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

    @patch('cake_radar.message_processor.assess_certainty')
    def test_edit_no_new_keywords_not_reforwarded(self, mock_assess):
        """Edited message already forwarded with same keywords should not be forwarded again."""
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 90, 'prompt_tokens': 10, 'completion_tokens': 5}

        # Original message is forwarded
        msg = {'text': 'cake in the kitchen', 'channel': 'C1', 'ts': '1000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)
        # Edit the message — same keywords, just minor rewording
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'there is cake in the kitchen!', 'ts': '1000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        # Should NOT have been re-evaluated
        self.assertEqual(mock_assess.call_count, 1)

    @patch('cake_radar.message_processor.assess_certainty')
    def test_edit_new_keyword_after_alert_is_not_reforwarded(self, mock_assess):
        """An already-alerted source message must never generate a second alert."""
        mock_say = MagicMock()
        mock_assess.return_value = {'decision': 'yes', 'total_certainty': 90, 'prompt_tokens': 10, 'completion_tokens': 5}

        # Original message forwarded with keyword 'cake'
        msg = {'text': 'cake in the office', 'channel': 'C1', 'ts': '2000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)

        # Even an edit that adds a new keyword must not create a second alert.
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'cake and baklava in the office', 'ts': '2000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        self.assertEqual(mock_assess.call_count, 1)

    @patch('cake_radar.message_processor.assess_certainty')
    @patch('cake_radar.message_processor.judge_decision')
    def test_normal_and_edit_events_only_forward_once_when_concurrent(self, mock_judge, mock_assess):
        """The original event and rapid edits must share one in-flight claim."""
        mock_say = MagicMock()
        entered_evaluation = threading.Event()
        release_evaluation = threading.Event()

        def assess(*_args, **_kwargs):
            entered_evaluation.set()
            release_evaluation.wait(timeout=2)
            return {'decision': 'yes', 'total_certainty': 90, 'reason': 'cake available'}

        mock_assess.side_effect = assess
        mock_judge.return_value = {'verdict': 'uphold', 'reason': 'food is available'}
        original = {'text': 'baklava at the round table', 'channel': 'C1', 'ts': '1000.00', 'channel_type': 'channel'}
        edit = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'channel_type': 'channel',
            'previous_message': {'ts': '1000.00'},
            'message': {'text': 'baklava at the round table', 'ts': '1000.00', 'files': []},
        }

        original_thread = threading.Thread(target=cake_radar.handle_message, args=(original, mock_say))
        original_thread.start()
        self.assertTrue(entered_evaluation.wait(timeout=1))
        edit_threads = [
            threading.Thread(target=cake_radar.handle_message_events, args=(edit, mock_say))
            for _ in range(2)
        ]
        for thread in edit_threads:
            thread.start()
        release_evaluation.set()
        original_thread.join(timeout=2)
        for thread in edit_threads:
            thread.join(timeout=2)

        self.assertEqual(mock_assess.call_count, 1)
        mock_say.assert_called_once()

    @patch('cake_radar.message_processor.assess_certainty')
    def test_edit_not_previously_forwarded_is_evaluated(self, mock_assess):
        """Edited message that was never forwarded should be evaluated normally."""
        mock_say = MagicMock()
        # First evaluation returns no — message not forwarded
        mock_assess.return_value = {'decision': 'no', 'total_certainty': 40, 'prompt_tokens': 10, 'completion_tokens': 5}

        msg = {'text': 'cake?', 'channel': 'C1', 'ts': '3000.00'}
        cake_radar.handle_message(msg, mock_say)
        self.assertEqual(mock_assess.call_count, 1)
        # Edit with same keywords — should be suppressed
        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'message': {'text': 'cake is in the kitchen!', 'ts': '3000.00', 'files': []}
        }
        cake_radar.handle_message_events(edit_event, mock_say)
        self.assertEqual(mock_assess.call_count, 1)

    @patch('cake_radar.message_processor.assess_certainty')
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

    @patch('cake_radar.message_processor.judge_decision')
    @patch('cake_radar.message_processor.assess_certainty')
    def test_edit_alert_links_to_original_message_ts(self, mock_assess, mock_judge):
        """Edited message alerts should link to the message ts, not the edit event ts."""
        mock_say = MagicMock()
        mock_assess.return_value = {
            'decision': 'yes',
            'total_certainty': 90,
            'reason': 'cake available',
            'prompt_tokens': 10,
            'completion_tokens': 5,
        }
        mock_judge.return_value = {'verdict': 'uphold', 'reason': 'food is available'}

        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C1',
            'channel_type': 'channel',
            'previous_message': {
                'text': 'rollout update',
                'ts': '1784732573.261519',
            },
            'message': {
                'text': "there's even :cake-radar: to celebrate",
                'ts': '1784733038.575069',
                'files': [],
            },
        }

        cake_radar.handle_message_events(edit_event, mock_say)

        mock_say.assert_called_once()
        alert_text = mock_say.call_args.kwargs['text']
        self.assertIn('/p1784732573261519', alert_text)
        self.assertNotIn('/p1784733038575069', alert_text)

    @patch('cake_radar.message_processor.assess_certainty')
    def test_thread_replies_ignored(self, mock_assess):
        """Verify that thread replies are ignored."""
        mock_say = MagicMock()
        
        # Thread reply message
        msg = {'text': 'cake', 'channel': 'C1', 'ts': '1000.00', 'thread_ts': '999.00'}
        cake_radar.handle_message(msg, mock_say)
        
        # Should NOT be processed
        self.assertEqual(mock_assess.call_count, 0)

    @patch('cake_radar.message_processor.assess_certainty')
    def test_cake_radar_channel_messages_are_ignored(self, mock_assess):
        """Cake Radar should never evaluate messages posted in its own alert channel."""
        mock_say = MagicMock()

        msg = {
            'text': ':green-light-blinker: *Cake Alert!* cake next to the coffee machine',
            'channel': 'C07RTPCLAKC',
            'ts': '1788253894.092969',
            'channel_type': 'channel',
        }
        cake_radar.handle_message(msg, mock_say)

        self.assertEqual(mock_assess.call_count, 0)
        mock_say.assert_not_called()

    @patch('cake_radar.message_processor.assess_certainty')
    def test_cake_radar_channel_edits_are_ignored(self, mock_assess):
        """Cake Radar should ignore edits to messages in its own alert channel."""
        mock_say = MagicMock()

        edit_event = {
            'subtype': 'message_changed',
            'channel': 'C07RTPCLAKC',
            'channel_type': 'channel',
            'previous_message': {
                'text': ':green-light-blinker: *Cake Alert!* cake next to the coffee machine',
                'ts': '1788253894.092969',
            },
            'message': {
                'text': ':green-light-blinker: *Cake Alert!* cake next to the coffee machine',
                'ts': '1788253894.092969',
                'files': [],
            },
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        self.assertEqual(mock_assess.call_count, 0)
        mock_say.assert_not_called()

    @patch('cake_radar.message_processor.assess_certainty')
    def test_private_channels_are_ignored(self, mock_assess):
        """Messages from private channels should never be evaluated or forwarded."""
        mock_say = MagicMock()

        msg = {'text': 'cake', 'channel': 'GPRIVATE', 'ts': '1000.00', 'channel_type': 'group'}
        cake_radar.handle_message(msg, mock_say)

        self.assertEqual(mock_assess.call_count, 0)
        mock_say.assert_not_called()

    @patch('cake_radar.message_processor.assess_certainty')
    def test_dms_are_ignored(self, mock_assess):
        """DMs should never be evaluated or forwarded."""
        mock_say = MagicMock()

        msg = {'text': 'cake', 'channel': 'DUSER', 'ts': '1000.00', 'channel_type': 'im'}
        cake_radar.handle_message(msg, mock_say)

        self.assertEqual(mock_assess.call_count, 0)
        mock_say.assert_not_called()

    @patch('cake_radar.message_processor.assess_certainty')
    def test_private_channel_edits_are_ignored(self, mock_assess):
        """Edited messages from private channels should never be evaluated or forwarded."""
        mock_say = MagicMock()

        edit_event = {
            'subtype': 'message_changed',
            'channel': 'GPRIVATE',
            'channel_type': 'group',
            'message': {'text': 'cake in the kitchen', 'ts': '1000.00', 'files': []},
        }
        cake_radar.handle_message_events(edit_event, mock_say)

        self.assertEqual(mock_assess.call_count, 0)
        mock_say.assert_not_called()

    def test_openai_auth_error_sends_operational_alert(self):
        """OpenAI auth failures should alert in the test/support channel."""
        error = Exception("Error code: 401 - {'error': {'code': 'invalid_api_key'}}")

        cake_radar.notify_openai_operational_error(error, 'classifier')
        cake_radar.notify_openai_operational_error(error, 'classifier')

        self.assertEqual(cake_radar._slack_app.client.chat_postMessage.call_count, 2)
        for call in cake_radar._slack_app.client.chat_postMessage.call_args_list:
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
            ['availability', 'false_positive', 'social_context', 'hungry'],
        )
        self.assertEqual(combined_prompt.count("clear reason to veto"), 4)
        self.assertIn("focus on availability", prompts['availability'])
        self.assertIn("shared amsterdam office location", prompts['availability'])
        self.assertIn("focus on known false positives", prompts['false_positive'])
        self.assertIn("otherwise uphold", prompts['false_positive'])
        self.assertIn("focus on social intent", prompts['social_context'])
        self.assertIn("informal sightings", prompts['social_context'])
        self.assertIn("focus on appetite and recall", prompts['hungry'])
        self.assertIn("hungry colleague would reasonably want", prompts['hungry'])
        self.assertIn("cake at entrance", prompts['hungry'])
        for category in (
            "future event",
            "non-food item",
            "out-of-scope location",
            "private/personal food",
            "idiom/metaphor",
            "birthday/congrats",
        ):
            self.assertIn(category, combined_prompt)

    @patch('cake_radar.message_processor._openai_client')
    def test_judge_panel_allows_one_overturn(self, mock_client):
        """One dissenting judge should not suppress an otherwise valid alert."""
        responses = []
        for content in (
            '{"verdict": "uphold", "reason": "current food in office"}',
            '{"verdict": "overturn", "reason": "no explicit offer"}',
            '{"verdict": "uphold", "reason": "reporting shared food"}',
            '{"verdict": "uphold", "reason": "hungry colleagues would want to know"}',
        ):
            response = MagicMock()
            response.choices[0].message.content = content
            responses.append(response)
        mock_client.chat.completions.create.side_effect = responses

        result = cake_radar.judge_decision("Hi, a cake :birthday: no name at the entrance!", "cake offered")

        self.assertEqual(result['verdict'], 'uphold')
        self.assertEqual(len(result['votes']), 4)
        self.assertEqual(mock_client.chat.completions.create.call_count, 4)
        for call in mock_client.chat.completions.create.call_args_list:
            self.assertEqual(call.kwargs['response_format'], {"type": "json_object"})

    @patch('cake_radar.message_processor._openai_client')
    def test_judge_panel_allows_two_overturns(self, mock_client):
        """Two overturn votes should still forward after adding the hungry judge."""
        responses = []
        for content in (
            '{"verdict": "overturn", "reason": "future event"}',
            '{"verdict": "overturn", "reason": "party invite"}',
            '{"verdict": "uphold", "reason": "mentions food"}',
            '{"verdict": "uphold", "reason": "hungry colleague would want to know"}',
        ):
            response = MagicMock()
            response.choices[0].message.content = content
            responses.append(response)
        mock_client.chat.completions.create.side_effect = responses

        result = cake_radar.judge_decision("Cake next Friday at the party", "cake mentioned")

        self.assertEqual(result['verdict'], 'uphold')
        self.assertEqual(len(result['votes']), 4)

    @patch('cake_radar.message_processor._openai_client')
    def test_judge_panel_requires_three_overturns_to_suppress(self, mock_client):
        """Three overturn votes should suppress a classifier yes."""
        responses = []
        for content in (
            '{"verdict": "overturn", "reason": "future event"}',
            '{"verdict": "overturn", "reason": "party invite"}',
            '{"verdict": "overturn", "reason": "not available now"}',
            '{"verdict": "uphold", "reason": "mentions food"}',
        ):
            response = MagicMock()
            response.choices[0].message.content = content
            responses.append(response)
        mock_client.chat.completions.create.side_effect = responses

        result = cake_radar.judge_decision("Cake next Friday at the party", "cake mentioned")

        self.assertEqual(result['verdict'], 'overturn')
        self.assertEqual(len(result['votes']), 4)

    def test_format_judge_votes_includes_each_outcome_and_reason(self):
        votes = [
            {'name': 'availability', 'verdict': 'uphold', 'reason': 'available now'},
            {'name': 'false_positive', 'verdict': 'overturn', 'reason': 'future event'},
            {'name': 'social_context', 'verdict': 'uphold', 'reason': 'informal sighting'},
            {'name': 'hungry', 'verdict': 'uphold', 'reason': 'worth knowing'},
        ]

        formatted = cake_radar.ai_classifier.format_judge_votes(votes)

        self.assertEqual(
            formatted,
            "availability=uphold (available now); "
            "false_positive=overturn (future event); "
            "social_context=uphold (informal sighting); "
            "hungry=uphold (worth knowing)",
        )

    @patch('cake_radar.message_processor.judge_decision')
    @patch('cake_radar.message_processor.assess_certainty')
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
                {'name': 'hungry', 'verdict': 'uphold', 'reason': 'worth knowing'},
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
        self.assertIn("hungry=uphold (worth knowing)", log_output)
