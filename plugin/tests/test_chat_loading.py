"""Conversation window and stream metadata on real isolated HTTP/journal paths."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode
from urllib.request import urlopen

import tests
import test_goal_http as fixtures
from crew import chat, event_sounds, presentation


class ChatLoadingTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.GoalHttpTests('test_starting_goal_keeps_general_native_available')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager = self.case.server.service.chat

    def history(self, **params):
        code, value, _ = self.case.request('/api/chat/history?' + urlencode({
            'project': 'demo', 'session_id': 's3', **params}))
        self.assertEqual(code, 200, value)
        return value

    def seed(self, count):
        path = self.manager.path('s3')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps({'seq': seq, 'type': 'message', 'id': str(seq),
                                           'text': 'input ' + str(seq)}) + '\n'
                                for seq in range(1, count + 1)), encoding='utf-8')

    def test_tail_before_pages_are_exclusive_and_unwindowed_contract_is_unchanged(self):
        self.seed(7)
        full = self.history()
        self.assertNotIn('first_seq', full)
        self.assertNotIn('has_more', full)
        for tail, before, expected, more in [(3, None, [5, 6, 7], True), (3, 5, [2, 3, 4], True),
                                             (3, 2, [1], False), (3, 1, [], False),
                                             (5000, 999, list(range(1, 8)), False)]:
            with self.subTest(tail=tail, before=before):
                page = self.history(tail=tail, **({} if before is None else {'before': before}))
                self.assertEqual([event['seq'] for event in page['events']], expected)
                self.assertEqual((page['first_seq'], page['has_more']), (expected[0] if expected else 0, more))
                self.assertEqual({key: value for key, value in page.items()
                                  if key not in ('events', 'first_seq', 'has_more')},
                                 {key: value for key, value in full.items() if key != 'events'})
        self.assertEqual(self.history(), full)
        self.assertEqual(self.history(before=5), full)

    def test_empty_and_maximum_tail_and_invalid_parameters(self):
        empty = self.history(tail=1)
        self.assertEqual((empty['events'], empty['first_seq'], empty['has_more']), ([], 0, False))
        self.seed(5001)
        page = self.history(tail=5000)
        self.assertEqual((len(page['events']), page['first_seq'], page['has_more']), (5000, 2, True))
        for params in ({'tail': 0}, {'tail': -1}, {'tail': 5001}, {'tail': '1.5'}, {'tail': ''},
                       {'tail': 1, 'before': 0}, {'tail': 1, 'before': 'bad'}):
            code, error, _ = self.case.request('/api/chat/history?' + urlencode({
                'project': 'demo', 'session_id': 's3', **params}))
            self.assertEqual(code, 400, (params, error))

    def stream_headers(self, **params):
        with urlopen(self.case.url + '/api/chat/events?' + urlencode({
                'project': 'demo', 'session_id': 's3', 'after': 0, **params}), timeout=5) as response:
            return response.headers

    def test_stream_headers_cover_empty_general_and_the_selected_task_execution(self):
        headers = self.stream_headers()
        self.assertEqual((headers['X-Crew-Execution-Id'], headers['X-Crew-Latest-Seq']), ('', '0'))
        goal = self.case.goal()
        empty = self.stream_headers(goal_id=goal['id'])
        self.assertEqual((empty['X-Crew-Execution-Id'], empty['X-Crew-Latest-Seq']), ('', '0'))
        code, receipt, _ = self.case.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'goal_id': goal['id'], 'text': 'Use the fake carrier'})
        self.assertEqual(code, 200, receipt)
        selected = receipt['execution_id']
        journal = self.manager.journal(selected)
        headers = self.stream_headers(goal_id=goal['id'])
        self.assertEqual(headers['X-Crew-Execution-Id'], selected)
        self.assertEqual(int(headers['X-Crew-Latest-Seq']), self.history(goal_id=goal['id'])['events'][-1]['seq'])
        page = self.history(goal_id=goal['id'], tail=1)
        full = self.history(goal_id=goal['id'])
        self.assertEqual(page['previous_executions'], full['previous_executions'])

    def test_headers_refresh_the_actual_journal_after_history_selection(self):
        self.seed(2)
        service = self.case.server.service
        original = service.chat_history

        def select_then_append(*args, **kwargs):
            result = original(*args, **kwargs)
            with self.manager.path('s3').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'seq': 3, 'type': 'message', 'id': 'external', 'text': 'external'}) + '\n')
            return result

        with mock.patch.object(service, 'chat_history', side_effect=select_then_append):
            headers = self.stream_headers()
        self.assertEqual((headers['X-Crew-Execution-Id'], headers['X-Crew-Latest-Seq']), ('', '3'))


class ProjectionCacheTests(unittest.TestCase):
    def test_projections_reuse_signature_and_invalidate_on_self_and_external_append_or_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = chat.Journal(Path(directory) / 'events.jsonl')
            journal.emit('text', id='answer', text='Saved reply')
            journal.emit('turn_result', status='completed', success=True)

            def replies(after=0):
                journal.refresh()
                key = (str(journal.path), journal.signature)
                return presentation.chat_replies(journal.events, after=after, cache_key=key), event_sounds.reply_completion(journal.events, cache_key=key)

            first = replies()
            self.assertEqual(first[0][0]['text'], 'Saved reply')
            self.assertEqual(first[1]['seq'], 2)
            with mock.patch('crew.presentation._chat_replies', side_effect=AssertionError('unchanged projection recomputed')):
                self.assertEqual(replies(), first)
                self.assertEqual(replies(after=1)[0], [])
            journal.emit('text', id='answer', text=' extended')
            self.assertEqual(replies()[0][0]['text'], 'Saved reply extended')
            with journal.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'seq': 4, 'type': 'assistant', 'id': 'external', 'content': [{'type': 'text', 'text': 'External reply'}]}) + '\n')
                stream.write(json.dumps({'seq': 5, 'type': 'turn_result', 'status': 'completed', 'success': True}) + '\n')
            changed = replies()
            self.assertEqual(changed[0][-1]['text'], 'External reply')
            self.assertEqual(changed[1]['seq'], 5)
            journal.path.write_text(json.dumps({'seq': 1, 'type': 'assistant', 'id': 'replacement', 'content': [{'type': 'text', 'text': 'Replacement'}]}) + '\n', encoding='utf-8')
            replacement = replies()
            self.assertEqual([reply['text'] for reply in replacement[0]], ['Replacement'])
            self.assertIsNone(replacement[1])

    def test_projection_cache_has_a_bounded_lru(self):
        cache = presentation.OrderedDict()
        for number in range(256):
            presentation.cached_projection(cache, (str(number), (number, number)), lambda: number)
        presentation.cached_projection(cache, ('0', (0, 0)), lambda: self.fail('live entry recomputed'))
        presentation.cached_projection(cache, ('256', (256, 256)), lambda: 256)
        self.assertEqual(len(cache), 256)
        self.assertIn(('0', (0, 0)), cache)
        self.assertNotIn(('1', (1, 1)), cache)
