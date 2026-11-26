import mimetypes
import sqlite3
from contextlib import closing
from pathlib import Path

from .. import board, channel, documents, goals, runtimes, store, workspace_settings
from .. import appearance
from .. import codex_binding, personal, native_skills
from ..web_api.common import _text


class ReadsMixin:
    def chat_project(self, conn, project_name, session_id):
        if project_name is None and personal.is_assistant(conn, session_id):
            return None
        return self.selected_project(conn, project_name)

    def chat_session(self, conn, project, session_id):
        session = board.session(conn, session_id)
        if session["project"] != project or not session['crew_name'] or session['status'] == 'closed' or board.is_archived(conn, session_id):
            raise board.BoardError("当前项目中没有这个网页会话")
        return session

    def chat_history(self, session_id, project_name=None, goal_id=None, *, include_previous=True):
        with closing(store.connect(self.home)) as conn:
            if goal_id is not None:
                goals.get(conn, goal_id, self.selected_project(conn, project_name))
                return self.chat.goal_controller.history(conn, goal_id, session_id, include_previous=include_previous)
            self.chat_session(conn, self.chat_project(conn, project_name, session_id), session_id)
            return {**self.chat.history(session_id), **self.chat.context_state(session_id, conn),
                    'read_seq': board.read_seq(conn, session_id)}

    def chat_skills(self, session_id, project_name=None, goal_id=None, refresh=False):
        """Skills the carrier behind this composer can invoke, from its own native discovery."""
        with closing(store.connect(self.home)) as conn:
            execution = None
            if goal_id is not None:
                goals.get(conn, goal_id, self.selected_project(conn, project_name))
                execution = self.chat.goal_controller.current(conn, goal_id, session_id)
                session = board.session(conn, session_id)
            else:
                session = self.chat_session(conn, self.chat_project(conn, project_name, session_id), session_id)
            runtime = execution['runtime'] if execution is not None else runtimes.get(conn, session_id)['runtime']
            codex_home = (codex_binding.home_for(conn, session_id, execution['id'] if execution else None)
                          if runtime == 'codex' else None)
        return native_skills.list_skills(runtime, session['cwd'], self.plugin_root, codex_home=codex_home,
                                         refresh=refresh)

    def channel_messages(self, project_name=None, limit=20, before=None):
        with closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, project_name)
            return {**channel.messages(conn, project, limit=limit, before=before), 'digest': channel.digest(conn, project)}

    def document_list(self, project_name=None, leader_session_id=None, goal_id=None):
        with self.write_lock, closing(store.connect(self.home)) as conn:
            project=self.selected_project(conn,project_name)
            if goal_id is not None:
                goals.get(conn, goal_id, project)
            try:
                scan=documents.refresh(conn,self.home,project)
            except (OSError, sqlite3.Error, ValueError) as exc:
                scan={'indexed':0,'scanned':0,'has_more':False,'errors':[{'source':'index','error':str(exc)}]}
            return {'documents':documents.list(conn,project,leader_session_id,goal_id=goal_id), 'scan':scan}

    def resolve_file(self, session_id, text, project_name=None, base=None, document_id=None):
        with closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, project_name)
            if document_id is not None:
                document=documents.get(conn,project,document_id)
                session_id=document['session_id']
                text=document['resolved_path']
                if text is None:
                    raise board.BoardError(document['file_error'])
                base=None
            root = Path(board.project(conn, project)["root"])
            allowed_roots=[root.resolve()]
            if document_id is not None:
                allowed_roots = workspace_settings.read_roots(conn, project, session_id)
            if session_id:
                session = board.session(conn, session_id)
                if session["project"] != project:
                    raise board.BoardError("\u4f1a\u8bdd\u4e0d\u5c5e\u4e8e\u5f53\u524d\u9879\u76ee")
                cwd = Path(session["cwd"])
                if session.get('worktree'):
                    allowed_roots.append(Path(session['worktree']).resolve())
            else:
                cwd = root
        text = _text({'path':text},'path')
        if base is not None and (not isinstance(base,str) or not any(store.is_under(Path(base).resolve(),allowed) for allowed in allowed_roots)):
            raise board.BoardError('文件来源不在当前项目或会话工作区内')
        path = Path(text.replace("\\", "/"))
        if not path.is_absolute():
            path = (Path(base).parent if base is not None else cwd) / path
            if base is None and not path.exists():
                path = root / text.replace("\\", "/")
        path = path.resolve()
        if not any(store.is_under(path,allowed) for allowed in allowed_roots):
            raise board.BoardError('文件路径不在当前项目或会话工作区内')
        exists = path.exists()
        error = "\u6587\u4ef6\u4e0d\u5b58\u5728\uff1a" + str(path) if not exists else "\u8fd9\u662f\u76ee\u5f55\uff1a" + str(path) if path.is_dir() else None
        suffix = path.suffix.lower()
        images = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
        text_documents = {".md", ".markdown", ".rst", ".adoc", ".txt", ".log", ".json", ".csv", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
        external_documents = {".html", ".htm", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt", ".ods", ".odp"}
        code = {".py", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".css", ".scss", ".ps1", ".bat", ".cmd", ".sh", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".vue", ".svelte", ".uproject", ".uplugin"}
        open_with = ("default" if suffix in text_documents | external_documents | images else
                     "vscode" if suffix in code else "blocked")
        kind = "image" if suffix in images else "markdown" if suffix in (".md", ".markdown") else "text" if suffix in text_documents | code else "external"
        result={"path": str(path), "exists": exists, "kind": kind,
                "type": "external" if kind == "external" else "preview",
                "open_with": open_with, "error": error}
        if document_id is not None:
            result.update(document_id=document_id,session_id=session_id)
        return result

    def read_file(self, info, raw=False):
        if info["error"] is not None:
            raise board.BoardError(info["error"])
        path = Path(info["path"])
        if info["kind"] == "image" and raw:
            return path.read_bytes(), mimetypes.guess_type(path)[0]
        if info["kind"] not in ("text", "markdown"):
            raise board.BoardError("\u6b64\u7c7b\u578b\u8bf7\u7528\u9ed8\u8ba4\u7a0b\u5e8f\u6253\u5f00")
        limit = 256 * 1024
        size = path.stat().st_size
        tail = path.suffix.lower() == ".log"
        with path.open("rb") as stream:
            if tail and size > limit:
                stream.seek(size - limit)
            content = stream.read(limit)
        return {**info, "content": content.decode("utf-8-sig", errors="replace"),
                "truncated": size > limit, "portion": "\u672b\u5c3e" if tail else "\u5f00\u5934"}, "application/json; charset=utf-8"

    def session_avatar(self, session_id):
        with closing(store.connect(self.home)) as conn:
            return appearance.avatar(conn, session_id)

    def chat_file(self, session_id, name, project_name=None, goal_id=None, execution_id=None):
        with closing(store.connect(self.home)) as conn:
            if goal_id:
                goals.get(conn, goal_id, self.selected_project(conn, project_name))
                execution = self.chat.goal_controller.current(conn, goal_id, session_id)
                if execution_id:
                    previous = goals.get_execution(conn, execution_id)
                    if previous['goal_id'] != goal_id or previous['session_id'] != session_id:
                        raise board.BoardError('附件执行不属于此任务会话')
                    execution = previous
                if execution is None:
                    raise board.BoardError('此目标还没有会话附件')
                session_id = execution['id']
            else:
                self.chat_session(conn, self.chat_project(conn, project_name, session_id), session_id)
            path, media_type = self.chat.file(session_id, name)
            return path.read_bytes(), media_type
