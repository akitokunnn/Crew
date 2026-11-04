"""Produce saved report HTML in a disposable home for the browser smoke check."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests
from crew import board, goals, store
from tests.test_goals import GoalFixture


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with closing(store.connect(root / 'home')) as conn:
            board.init_project(conn, 'fixture', root)
            board.join(conn, 'leader', 'fixture', 'Report leader', root, root, None, role='leader')
            goal = goals.create(conn, 'fixture', 'leader', '验收报告测试', '核对实测步骤', [{'id': 'short', 'title': '步骤与预期可以阅读'}])
            data = GoalFixture.report(None, 'awaiting_acceptance')
            data['user_test'] = [{'step': '打开 <设备页>，输入 A & B。', 'expected': '看到“准备就绪”。'},
                                 {'step': '点击保存。', 'expected': '看到“已保存”。'}]
            standard = goals.save_report(conn, goal['id'], data)
            authored = goals.save_report(conn, goal['id'], {**data, 'document_html': '<h1>验收报告测试</h1><p>独立报告正文。</p>'})
            print(json.dumps({'standard': standard['html'], 'authored': authored['html']}, ensure_ascii=False))
    assert not root.exists()


if __name__ == '__main__':
    main()
