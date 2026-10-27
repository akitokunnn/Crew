"""Remote inboxes run against explicit workspace/session ports, without Service."""
import tests
import contextlib
import json
import threading
from types import SimpleNamespace
from unittest import mock

from crew import board, notifications, notification_ports, session_presence, store
from crew.execution_host import ExecutionStore
from test_core import CoreCase, NOW
from test_notifications import FakeQQ


class NotificationPortsTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.sid = 'explicit-leader'
        board.join(self.conn, self.sid, 'demo', '领队', self.root, self.root, 'main', role='leader', now=NOW)
        self.conn.execute('UPDATE sessions SET spawn_id=? WHERE session_id=?', (self.sid, self.sid))
        (self.home / 'qq.json').write_text(json.dumps({'app_id': 'fake', 'app_secret': 'fake'}), encoding='utf-8')
        (self.home / 'notify.json').write_text(json.dumps({
            'owner_openid': 'owner', 'since': NOW, 'manual_away': False,
            'current_leader_session_id': self.sid, 'leader_numbers': {self.sid: 1}}), encoding='utf-8')
        (self.home / 'spawn').mkdir(exist_ok=True)
        (self.home / 'spawn' / (self.sid + '.json')).write_text('{}', encoding='utf-8')
        self.executions = ExecutionStore(self.home)
        self.journal = self.executions.journal(self.sid)
        self.journal.set_status('idle')
        self.executions.register(self.sid, SimpleNamespace(process=SimpleNamespace(poll=lambda: None)))
        self.send = mock.Mock(side_effect=self.feed)
        workspaces = notification_ports.WorkspaceQueries(lambda: store.parse_iso(NOW), lambda project: [],
            lambda: session_presence.Observation((), None, True), lambda conn, agents: list(agents))
        conversations = notification_ports.Conversations(self.executions,
            lambda background=False: contextlib.nullcontext(True), self.send, lambda sid: True)
        self.inbox = notifications.Inbox(self.home, threading.RLock(), workspaces, conversations,
                                         FakeQQ(), lock_status=lambda: False)
        self.addCleanup(self.inbox.close)

    def feed(self, session, text, *, images, source, identity):
        self.journal.message(identity, text, source, images=images)
        self.journal.set_status('working')
        return {'accepted': True}

    def receive(self, text):
        self.inbox.receive('message', {'message_id': 'original-message', 'openid': 'owner',
            'text': text, 'quoted_ref': None, 'attachments': []})

    def test_inbox_queues_then_feeds_original_input_without_an_application_container(self):
        text = '保留这句原话\n以及第二行。'
        self.receive(text)
        self.send.assert_not_called()
        self.inbox.process_replies()
        args, kwargs = self.send.call_args
        self.assertEqual(args[0]['session_id'], self.sid)
        self.assertEqual(args[1], text)
        self.assertEqual(kwargs['source'], 'qq')
        self.assertEqual(self.inbox.execute(self.conn,
            'SELECT status FROM notification_replies').fetchone()[0], 'running')

    def test_refused_admission_keeps_the_reply_queued_and_does_not_feed_native_input(self):
        self.receive('更新完成后再发送。')
        self.inbox.conversations = notification_ports.Conversations(self.executions,
            lambda background=False: contextlib.nullcontext(False), self.send, lambda sid: True)
        self.inbox.process_replies()
        self.send.assert_not_called()
        self.assertEqual(self.inbox.execute(self.conn,
            'SELECT status FROM notification_replies').fetchone()[0], 'queued')
