import os
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ['SLACK_BOT_TOKEN'] = 'xoxb-dummy'
os.environ['SLACK_SIGNING_SECRET'] = 'dummy'
os.environ['OPENAI_API_KEY'] = 'dummy'
os.environ['SLACK_TOKEN_VERIFICATION_ENABLED'] = 'false'

from cake_radar import message_processor, summon

BOT = 'UBOT'
CHANNEL = 'C1'
ALERT_CHANNEL_ID = 'C07RTPCLAKC'
YES = {'decision': 'yes', 'total_certainty': 70, 'reason': 'cake', 'prompt_tokens': 1, 'completion_tokens': 1}
NO = {'decision': 'no', 'total_certainty': 80, 'reason': 'no food', 'prompt_tokens': 1, 'completion_tokens': 1}


def _now_ts(offset=0):
    return f"{time.time() + offset:.6f}"


class SummonTestCase(unittest.TestCase):

    def setUp(self):
        message_processor._message_states.clear()
        summon._handled_mentions.clear()
        summon._channel_public_cache.clear()
        message_processor.Config.CAKE_RADAR_CHANNEL_ID = ALERT_CHANNEL_ID
        self.slack_app = MagicMock()
        message_processor.configure(self.slack_app, MagicMock())
        self.client = self.slack_app.client
        self.client.auth_test.return_value = {'user_id': BOT}
        self.private_channels = set()
        self.alert_history = []
        self.channel_history = []
        self.thread = []

        def history(channel, **kwargs):
            if channel == ALERT_CHANNEL_ID:
                return {'messages': self.alert_history}
            return {'messages': self.channel_history}

        def info(channel):
            return {'channel': {'name': 'general', 'is_private': channel in self.private_channels}}

        self.client.conversations_history.side_effect = history
        self.client.conversations_replies.side_effect = lambda **kwargs: {'messages': self.thread}
        self.client.conversations_info.side_effect = info
        self.client.users_info.return_value = {'user': {'profile': {'display_name': 'ties'}}}

        patcher = patch('cake_radar.message_processor.download_slack_images', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def reactions(self):
        return [c.kwargs['name'] for c in self.client.reactions_add.call_args_list]

    def alerts(self):
        return [c.kwargs for c in self.client.chat_postMessage.call_args_list]

    def mention(self, ts='101.000001', thread_ts='100.000001', user='UJOACHIM', channel=CHANNEL):
        summon.handle_app_mention({'channel': channel, 'ts': ts, 'thread_ts': thread_ts, 'user': user}, MagicMock())


class TestLiveSummon(SummonTestCase):

    @patch('cake_radar.message_processor.judge_decision')
    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_thread_mention_crossposts_parent(self, mock_assess, mock_judge):
        self.thread = [
            {'ts': '100.000001', 'user': 'UJOACHIM', 'text': 'Happy joachim day! Do get a piece of cake today!!'},
            {'ts': '101.000001', 'user': 'UJOACHIM', 'text': f'<@{BOT}>', 'thread_ts': '100.000001'},
        ]
        self.mention()

        # Only the parent is a candidate; the mention-only reply is skipped
        self.assertEqual(mock_assess.call_count, 1)
        self.assertEqual(mock_assess.call_args.kwargs['extra_context'], summon.Config.SUMMON_PROMPT_HINT)
        mock_judge.assert_not_called()
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('/p100000001', self.alerts()[0]['text'])
        self.client.reactions_add.assert_called_once_with(channel=CHANNEL, timestamp='101.000001', name='cake-radar')
        self.assertTrue(message_processor.was_forwarded(CHANNEL, '100.000001'))

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_already_in_alert_channel_history(self, mock_assess):
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake in the kitchen'}]
        self.alert_history = [{'text': f':cake-radar: *<https://slack.com/archives/{CHANNEL}/p100000001|Cake detected!>*'}]
        self.mention()

        mock_assess.assert_not_called()
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.reactions(), ['cake-radar'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_already_forwarded_by_live_flow(self, mock_assess):
        message_processor.claim_forward(CHANNEL, '100.000001')
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake in the kitchen'}]
        self.mention()

        mock_assess.assert_not_called()
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.reactions(), ['cake-radar'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=NO)
    def test_no_treat_reacts_x(self, mock_assess):
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake?'}]
        self.mention()
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.reactions(), ['x'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_no_candidates_reacts_x(self, mock_assess):
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'release notes for today'}]
        self.mention()
        mock_assess.assert_not_called()
        self.assertEqual(self.reactions(), ['x'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_private_channel_is_never_crossposted(self, mock_assess):
        self.private_channels.add(CHANNEL)
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake in the kitchen'}]
        self.mention()
        mock_assess.assert_not_called()
        self.client.conversations_replies.assert_not_called()
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.reactions(), ['x'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_failed_post_reacts_x_and_can_be_retried(self, mock_assess):
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake'}]
        self.client.chat_postMessage.side_effect = Exception('boom')
        self.mention()
        self.assertEqual(self.reactions(), ['x'])
        self.assertFalse(message_processor.was_forwarded(CHANNEL, '100.000001'))

    @patch('cake_radar.message_processor.assess_certainty')
    def test_summon_threshold_used(self, mock_assess):
        threshold = summon.Config.SUMMON_CERTAINTY_THRESHOLD
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake'}]

        mock_assess.return_value = dict(YES, total_certainty=threshold)
        self.mention(ts='101.000001')
        self.assertEqual(self.reactions(), ['x'])

        mock_assess.return_value = dict(YES, total_certainty=threshold + 1)
        self.mention(ts='102.000001')
        self.assertEqual(self.reactions(), ['x', 'cake-radar'])

    @patch('cake_radar.message_processor.assess_certainty')
    def test_picks_highest_certainty_and_links_thread_reply(self, mock_assess):
        self.thread = [
            {'ts': '100.000001', 'user': 'U1', 'text': 'cake soon'},
            {'ts': '100.500001', 'user': 'U2', 'text': 'cake is in the kitchen', 'thread_ts': '100.000001'},
        ]
        mock_assess.side_effect = [dict(YES, total_certainty=65), dict(YES, total_certainty=95)]
        self.mention()
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('/p100500001?thread_ts=100.000001', self.alerts()[0]['text'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_top_level_mention_scans_today_including_the_tag(self, mock_assess):
        self.channel_history = [{'ts': '200.000001', 'user': 'U1', 'text': f'<@{BOT}> cake in the kitchen!'}]
        self.mention(ts='200.000001', thread_ts=None, user='U1')

        call = next(c for c in self.client.conversations_history.call_args_list if c.kwargs['channel'] == CHANNEL)
        self.assertEqual(call.kwargs['latest'], '200.000001')
        self.assertEqual(float(call.kwargs['oldest']), summon._start_of_today_ts())
        self.assertEqual(call.kwargs['limit'], summon.Config.SUMMON_LOOKBACK_MESSAGES + 1)
        self.assertIn('/p200000001', self.alerts()[0]['text'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_ignored_in_alert_channel(self, mock_assess):
        self.mention(channel=ALERT_CHANNEL_ID)
        self.client.conversations_replies.assert_not_called()
        self.client.conversations_history.assert_not_called()
        self.assertEqual(self.reactions(), [])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_same_mention_handled_once(self, mock_assess):
        self.thread = [{'ts': '100.000001', 'user': 'U1', 'text': 'cake'}]
        self.mention()
        summon.run_summon(CHANNEL, '101.000001', '100.000001', source='join')
        self.assertEqual(len(self.alerts()), 1)
        self.assertEqual(self.reactions(), ['cake-radar'])

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_top_level_bot_mention_skipped_by_live_flow(self, mock_assess):
        message_processor.handle_message({'text': f'<@{BOT}> cake!', 'channel': CHANNEL, 'ts': '300.0'}, MagicMock())
        mock_assess.assert_not_called()


class TestJoinCatchUp(SummonTestCase):

    @patch('cake_radar.summon.run_summon')
    def test_join_finds_thread_and_top_level_mentions(self, mock_run):
        parent_ts = _now_ts(-2700)
        thread_mention_ts = _now_ts(-2640)
        top_mention_ts = _now_ts(-60)
        self.channel_history = [
            {'ts': top_mention_ts, 'user': 'U2', 'text': f'<@{BOT}> donuts here'},
            {'ts': parent_ts, 'user': 'U1', 'text': 'cake!', 'reply_count': 1, 'latest_reply': thread_mention_ts},
        ]
        self.thread = [
            {'ts': parent_ts, 'user': 'U1', 'text': 'cake!'},
            {'ts': thread_mention_ts, 'user': 'U1', 'text': f'<@{BOT}>'},
        ]
        summon.handle_member_joined({'user': BOT, 'channel': CHANNEL}, MagicMock())

        calls = [c.args for c in mock_run.call_args_list]
        self.assertEqual(calls, [
            (CHANNEL, thread_mention_ts, parent_ts, 'U1'),
            (CHANNEL, top_mention_ts, '', 'U2'),
        ])

    @patch('cake_radar.summon.run_summon')
    def test_join_ignores_old_mentions(self, mock_run):
        old = _now_ts(-summon.Config.SUMMON_JOIN_LOOKBACK_SECONDS - 600)
        self.channel_history = [{'ts': old, 'user': 'U1', 'text': f'<@{BOT}> cake'}]
        summon.handle_member_joined({'user': BOT, 'channel': CHANNEL}, MagicMock())
        mock_run.assert_not_called()

    @patch('cake_radar.summon.run_summon')
    def test_join_by_other_user_does_nothing(self, mock_run):
        self.channel_history = [{'ts': _now_ts(-60), 'user': 'U1', 'text': f'<@{BOT}> cake'}]
        summon.handle_member_joined({'user': 'USOMEONE', 'channel': CHANNEL}, MagicMock())
        mock_run.assert_not_called()
        self.client.conversations_history.assert_not_called()

    @patch('cake_radar.message_processor.assess_certainty', return_value=YES)
    def test_join_without_mentions_posts_nothing(self, mock_assess):
        self.channel_history = [{'ts': _now_ts(-60), 'user': 'U1', 'text': 'cake in the kitchen'}]
        summon.handle_member_joined({'user': BOT, 'channel': CHANNEL}, MagicMock())
        mock_assess.assert_not_called()
        self.assertEqual(self.alerts(), [])


if __name__ == '__main__':
    unittest.main()
