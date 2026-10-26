"""Authored documents stay intact; acceptance and isolation remain Crew-owned."""
from html import unescape
from html.parser import HTMLParser

from crew import board, goals, report_documents
from tests.test_goals import GoalFixture


class Frames(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.frames = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == 'iframe':
            self.frames.append(dict(attrs))


class ReportDocumentTests(GoalFixture):
    def test_custom_document_is_snapshotted_in_a_separate_frame_with_collapsed_evidence(self):
        goal = self.goal()
        document = '<!doctype html><title>选哪个？</title><style>h1{color:teal}</style><h1>先说结论</h1><button onclick="this.textContent=\'已筛选\'">筛选</button><script>window.ready=true</script>'
        data = {**self.report(), 'document_html': document}
        saved = goals.save_report(self.conn, goal['id'], data)
        self.assertEqual(saved['data']['document_html'], document)
        frame, = Frames(saved['html']).frames
        self.assertEqual(frame['sandbox'], 'allow-scripts')
        self.assertTrue(frame['srcdoc'].endswith(document))
        self.assertIn("default-src 'none'", unescape(frame['srcdoc']))
        self.assertNotIn('<script>', saved['html'])
        self.assertIn('<details class="crew-evidence">', saved['html'])
        self.assertIn('measurements/sample-1.json', saved['html'])
        self.assertIn('输入 / 操作', saved['html'])
        data['document_html'] = '<h1>第二稿</h1>'
        again = goals.save_report(self.conn, goal['id'], data)
        self.assertIn('第二稿', again['html'])
        self.assertEqual(goals.get_report(self.conn, saved['id'])['html'], saved['html'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)

    def test_document_validation_does_not_replace_real_acceptance(self):
        goal = self.goal()
        for document in (None, {}, 'a' * (report_documents.MAX_HTML + 1), '中' * (report_documents.MAX_HTML // 3 + 1)):
            with self.subTest(document_type=type(document)), self.assertRaises(board.BoardError):
                goals.save_report(self.conn, goal['id'], {**self.report(), 'document_html': document})
        data = {**self.report(), 'document_html': '<h1>全部成功</h1>'}
        data['acceptance'][0]['status'] = 'unverified'
        with self.assertRaisesRegex(board.BoardError, '不能宣称目标达标'):
            goals.save_report(self.conn, goal['id'], data)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)

    def test_legacy_or_empty_document_still_renders_the_existing_report(self):
        goal = self.goal()
        previous = goals.render_report(self.conn, goal['id'], self.report())
        self.assertEqual(goals.render_report(self.conn, goal['id'], {**self.report(), 'document_html': ''}), previous)
        self.assertNotIn('<iframe', previous)
