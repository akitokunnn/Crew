"""Read conversation histories and attachments with explicit workspace/scope checks."""
from contextlib import closing
from dataclasses import dataclass

from . import board, goals, store


@dataclass
class Queries:
    home: object
    selected_project: object
    executions: object
    goal_history: object
    current_execution: object
    attachment_file: object

    def session(self, conn, project, session_id):
            session = board.session(conn, session_id)
            if session["project"] != project or not session['crew_name'] or session['status'] == 'closed' or board.is_archived(conn, session_id):
                raise board.BoardError("当前项目中没有这个网页会话")
            return session

    def history(self, session_id, project_name=None, goal_id=None, *, include_previous=True):
            with closing(store.connect(self.home)) as conn:
                if goal_id:
                    goals.get(conn, goal_id, self.selected_project(conn, project_name))
                    return self.goal_history(conn, goal_id, session_id, include_previous=include_previous)
                self.session(conn, self.selected_project(conn, project_name), session_id)
                return {**self.executions.history(session_id), 'read_seq': board.read_seq(conn, session_id)}

    def file(self, session_id, name, project_name=None, goal_id=None, execution_id=None):
            with closing(store.connect(self.home)) as conn:
                if goal_id:
                    goals.get(conn, goal_id, self.selected_project(conn, project_name))
                    execution = self.current_execution(conn, goal_id, session_id)
                    if execution_id:
                        previous = goals.get_execution(conn, execution_id)
                        if previous['goal_id'] != goal_id or previous['session_id'] != session_id:
                            raise board.BoardError('附件执行不属于此任务会话')
                        execution = previous
                    if execution is None:
                        raise board.BoardError('此目标还没有会话附件')
                    session_id = execution['id']
                else:
                    self.session(conn, self.selected_project(conn, project_name), session_id)
                path, media_type = self.attachment_file(session_id, name)
                return path.read_bytes(), media_type
