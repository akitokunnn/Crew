"""File access and downloads HTTP routes."""
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from .. import board, goals, store
from .. import native_folders
from .. import event_sounds
from .common import _text


def post_files(service, path, data, project_name):
    if project_name is not None and data.get('project') is not None and data['project'] != project_name:
        raise board.BoardError('请求正文和地址中的项目不一致')
    info = service.resolve_file(data.get("session_id"), None if 'document_id' in data else _text(data, "path"),
                             project_name if project_name is not None else data.get("project"), data.get("base"), data.get('document_id'))
    if path == "/api/files/resolve":
        return info
    if info["error"] is not None:
        raise board.BoardError(info["error"])
    if sys.platform not in ("win32", "darwin"):
        raise board.BoardError("当前系统暂不支持从 Crew 打开文件")
    if path == "/api/files/open":
        if info["open_with"] == "blocked":
            raise board.BoardError("这类文件不直接打开，请用‘在文件夹中显示’")
        target = info["path"]
        if info["open_with"] == "vscode":
            uri_path = "/" + Path(target).as_posix() if sys.platform == "win32" else target
            target = "vscode://file" + quote(uri_path, safe="/:")
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", info["path"]])
    else:
        subprocess.Popen(["explorer", "/select," + info["path"]])
    return {"ok": True}


def post_goals_report_folder(service, path, data, project_name):
    # The remote route rules reserve native folder opening for the host.
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, project_name if project_name is not None else data.get('project'))
        goal_id = _text(data, 'goal_id')
        goals.get(conn, goal_id, project)
        report = goals.get_report(conn, _text(data, 'report_id'))
        if report['goal_id'] != goal_id:
            raise board.BoardError('报告不属于此目标')
        folder = goals.report_folder(board.project(conn, project)['root'], report['data'])
    if not os.path.isdir(folder['path']):
        name = '交付文件夹' if folder['declared'] or folder.get('inferred') else '工作区文件夹'
        raise board.BoardError(name + '不存在：' + folder['path'])
    if sys.platform == "darwin":
        subprocess.Popen(["open", folder['path']])
    elif sys.platform == "win32":
        try:
            native_folders.open_windows(folder['path'])
        except OSError as exc:
            raise board.BoardError(str(exc)) from exc
    else:
        raise board.BoardError("当前系统暂不支持从 Crew 打开文件夹")
    return {'ok': True, **folder}


def get_workspace_data_artifact(handler, service, url, query):
    file, metadata = service.storage.artifact(query['workspace_id'][0],query['version_id'][0])
    handler._stream_storage_file(file, metadata['mime_type'], sandbox=True)


def get_workspace_data_blob(handler, service, url, query):
    file, metadata = service.storage.blob(query['workspace_id'][0],query['blob_id'][0])
    handler._stream_storage_file(file, metadata['mime_type'], sandbox=True)


def get_workspace_data_download(handler, service, url, query):
    file = service.storage.downloads.get(query.get('id',[''])[0])
    if file is None:
        raise ValueError('导出文件已失效，请重新导出')
    handler._stream_storage_file(file, 'application/zip', attachment='workspace.crewpack')


def get_file_content_or_download(handler, service, url, query):
    hosts = {"127.0.0.1:%d" % handler.server.server_port, "localhost:%d" % handler.server.server_port}
    remote = getattr(handler.server, 'remote_controller', None)
    remote_files = remote is not None and handler._request_active()
    if not remote_files and handler.headers.get_all("Host") not in ([host] for host in hosts):
        handler._send(403, {"error": "\u6587\u4ef6\u8bfb\u53d6\u53ea\u5141\u8bb8\u672c\u673a Host"})
        return
    document_id=int(query['document_id'][0]) if 'document_id' in query else None
    info = service.resolve_file(query.get("session_id", [None])[0], query.get("path",[None])[0],
                                query.get("project", [None])[0], query.get("base", [None])[0],document_id)
    if url.path == "/api/files/download":
        # Another device cannot open host files with a local program; it saves the original bytes instead.
        if info["error"] is not None:
            raise board.BoardError(info["error"])
        path = Path(info["path"])
        handler._stream_storage_file(path, "application/octet-stream", attachment=path.name, sandbox=True)
        return
    content, media_type = service.read_file(info, raw=query.get("raw") == ["1"])
    handler.send_file_content(content, media_type)


def get_sounds_custom_file(handler, service, url, query):
    handler._stream_storage_file(*event_sounds.custom_file(service.home, query.get('id', [''])[0]))


def get_chat_file(handler, service, url, query):
    content, media_type = service.chat_file(query.get('session_id', [''])[0], query.get('name', [''])[0],
                                            query.get('project', [None])[0], query.get('goal_id', [None])[0],
                                            query.get('execution_id', [None])[0])
    handler._send(200, content, media_type)
