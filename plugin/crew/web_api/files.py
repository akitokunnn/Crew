"""Files HTTP interface; dependencies are supplied explicitly at composition."""
from pathlib import Path
from urllib.parse import quote
import os
import subprocess
from .. import board
from .database import text as _text
from .registry import Content


def register(routes, db, read_file, resolve_file):
    def post_open_file(request):
        data, path, project_name = request.data, request.path, request.project
        if project_name is not None and data.get('project') is not None and data['project'] != project_name:
            raise board.BoardError('请求正文和地址中的项目不一致')
        info = resolve_file(data.get("session_id"), None if 'document_id' in data else _text(data, "path"),
                                 project_name if project_name is not None else data.get("project"), data.get("base"), data.get('document_id'))
        if path == "/api/files/resolve":
            return info
        if info["error"] is not None:
            raise board.BoardError(info["error"])
        if path == "/api/files/open":
            if info["open_with"] == "blocked":
                raise board.BoardError("这类文件不直接打开，请用‘在文件夹中显示’")
            target = ("vscode://file/" + quote(Path(info["path"]).as_posix(), safe="/:")
                      if info["open_with"] == "vscode" else info["path"])
            os.startfile(target)
        else:
            subprocess.Popen(["explorer", "/select," + info["path"]])
        return {"ok": True}

    routes.add('POST', '/api/files/resolve', post_open_file)
    routes.add('POST', '/api/files/open', post_open_file)
    routes.add('POST', '/api/files/reveal', post_open_file)

    def get_content(request):
        query = request.query
        document_id = int(query['document_id'][0]) if 'document_id' in query else None
        info = resolve_file(query.get('session_id', [None])[0], query.get('path', [None])[0], query.get('project', [None])[0], query.get('base', [None])[0], document_id)
        content, media_type = read_file(info, raw=query.get('raw') == ['1'])
        return Content(content, media_type, sandbox=True)

    routes.add('GET', ['/api/files/content'], get_content, host_only=True)
