"""Adapters that run the existing ``web_api`` route functions as operation handlers.

Each function still receives the path and discriminators of the URL it was
written for; that URL itself is no longer served (only ``/api/op`` is).
"""
from dataclasses import dataclass
from urllib.parse import SplitResult, urlencode


@dataclass
class Content:
    """A non-JSON response captured from a legacy GET handler outside HTTP."""
    body: bytes
    media_type: str
    attachment: str = None


class Capture:
    """Stands in for the HTTP handler when a GET route runs for MCP or the CLI."""

    def __init__(self):
        self.value = None

    def _send(self, code, content, content_type='application/json; charset=utf-8', attachment=None, cache_control=None):
        self.value = content if isinstance(content, (dict, list)) else Content(content, content_type, attachment)

    def send_file_content(self, content, media_type, *, interactive_report=False):
        self.value = Content(content, media_type)

    def _stream_storage_file(self, path, media_type, *, attachment=None, sandbox=False):
        self.value = Content(path.read_bytes(), media_type, attachment)


def post(function, path, when=()):
    def handler(ctx, args):
        data = {**dict(when), **ctx.body(args)}
        if ctx.op.lock == 'none':
            return function(ctx.service, path, data, ctx.project_name)
        if ctx.op.lock == 'outer':
            return function(ctx.service, path, data, ctx.project_name, ctx.conn, ctx.now)
        return function(ctx.service, path, data, ctx.project_name, ctx.conn, ctx.now, ctx.project)
    return handler


def get(function, path, when=()):
    from .executor import SENT

    def handler(ctx, args):
        query = ctx.query if ctx.query is not None else {
            key: [str(item) for item in value] if isinstance(value, list) else
            ['1' if value is True else '0' if value is False else str(value)]
            for key, value in args.items()}
        query = {**{key: [value] for key, value in when}, **query}
        if ctx.project_name is not None and 'project' not in query:
            query = {**query, 'project': [ctx.project_name]}
        url = SplitResult('', '', path, urlencode(query, doseq=True), '')
        if ctx.http is not None:
            function(ctx.http, ctx.service, url, query)
            return SENT
        capture = Capture()
        function(capture, ctx.service, url, query)
        return capture.value
    return handler
