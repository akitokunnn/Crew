"""Publish only bundled frontend assets, with one policy for HTTP and fixtures.

Adding a frontend script or stylesheet does not require a second Python route
entry. The catalog belongs to the selected backend source, never to the user
workspace or HOME. HTML documents remain explicit application entry points.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import threading
from urllib.parse import urlsplit


_MEDIA = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
}
_HTML = 'text/html; charset=utf-8'
_NO_STORE = 'no-store'
_CACHE = 'public, max-age=604800'


def _file(root, relative):
    candidate = root / relative
    parts = candidate.relative_to(root).parts
    if any((root.joinpath(*parts[:index])).is_symlink() for index in range(1, len(parts) + 1)):
        raise ValueError('Frontend assets cannot be symbolic links: ' + relative)
    if not candidate.resolve().is_relative_to(root):
        raise ValueError('Frontend asset escaped its source directory: ' + relative)
    return candidate


@dataclass(frozen=True)
class Asset:
    root: Path
    relative: str
    content_type: str
    cache_control: str = _NO_STORE

    def read_bytes(self):
        return _file(self.root, self.relative).read_bytes()


class _References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.paths.append(attrs['src'])
        elif tag == 'link' and attrs.get('href') and attrs.get('rel') in ('stylesheet', 'icon'):
            self.paths.append(attrs['href'])


class AssetCatalog:
    """A source-scoped, immutable catalog, loaded when an asset is first needed."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._assets = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._assets is not None:
                return self._assets
            assets = {}
            for path in sorted(self.root.rglob('*')):
                relative = path.relative_to(self.root)
                if path.suffix not in _MEDIA or any(part.startswith('.') for part in relative.parts):
                    continue
                checked = _file(self.root, relative.as_posix())
                if not checked.is_file():
                    continue
                cache = _CACHE if path.suffix == '.woff2' or relative.as_posix() == 'logo.svg' else _NO_STORE
                assets['/' + relative.as_posix()] = Asset(self.root, relative.as_posix(), _MEDIA[path.suffix], cache)
            for entry in ('index.html', 'storage-history.html'):
                parser = _References()
                parser.feed(_file(self.root, entry).read_text(encoding='utf-8'))
                for reference in parser.paths:
                    url = urlsplit(reference)
                    if url.scheme or url.netloc or not url.path.startswith('/') or url.path not in assets:
                        raise ValueError(f'{entry} references an unavailable bundled asset: {reference}')
            assets['/'] = Asset(self.root, 'index.html', _HTML)
            self._assets = assets
            return assets

    def validate(self):
        """Check both entry pages before packaging or opening a browser."""
        self._load()

    def resolve(self, path, *, history=False):
        # Unknown application routes should remain 404 even for a headless API
        # instance whose selected source does not contain a frontend directory.
        if path != '/' and Path(path).suffix not in _MEDIA:
            return None
        asset = self._load().get(path)
        if path == '/' and history:
            return Asset(self.root, 'storage-history.html', _HTML)
        return asset

    @property
    def routes(self):
        """Serializable route snapshot for isolated UI fixtures and diagnostics."""
        return {path: (asset.relative, asset.content_type) for path, asset in self._load().items()}
