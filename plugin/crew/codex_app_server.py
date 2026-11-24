"""Provision the official standalone app-server without changing the user's Codex installation."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

from . import board


VERSION = '0.172.0'
RELEASE_URL = 'https://github.com/openai/codex/releases/download/rust-v' + VERSION
TARGETS = {
    'Windows': 'pc-windows-msvc',
    'Darwin': 'apple-darwin',
    'Linux': 'unknown-linux-musl',
}
# SHA-256 of each official release's complete app-server package (including platform helpers).
PACKAGE_SHA256 = {
    'x86_64-pc-windows-msvc': '3375f63216cbc9584c8244aeef3ec21d89374b57c1174b958cbfdb931ebfc4b7',
    'aarch64-pc-windows-msvc': 'a6999fec6820265f5d1dd51ac62668b38d3fb7f682f4ee780ef91a15ab5d6508',
    'x86_64-apple-darwin': 'd4dce25421a0e33620a7a1e95c6a903a0cd9a56087cedfcb8239d4ec3330541c',
    'aarch64-apple-darwin': 'cce65e6b13dad10a7992e1607e52376d37a1f3c2dea53d8ee3c0c42367d07b4b',
    'x86_64-unknown-linux-musl': '5dca8bb097be5dc3844d167d6d5276f92be11b2b894a87cfce4332a7514c2731',
    'aarch64-unknown-linux-musl': 'b012a69a2329e9cae4f7a6a9f3cf6c371e3866dc05608e3207249d4707e350ab',
}


def command(home):
    system = platform.system()
    machine = platform.machine().lower()
    arch = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)
    target = f'{arch}-{TARGETS.get(system)}'
    digest = PACKAGE_SHA256.get(target)
    if digest is None:
        raise board.BoardError(f'当前平台没有 Crew 支持的 Codex app-server 发布包（{system} {machine}）')
    cache = Path(home) / 'runtime-cache' / 'codex-app-server' / VERSION / target
    entrypoint = Path('bin') / ('codex-app-server.exe' if system == 'Windows' else 'codex-app-server')
    executable = cache / entrypoint
    if not executable.is_file():
        try:
            _install(cache, entrypoint, target, digest)
        except (OSError, tarfile.TarError, ValueError) as exc:
            raise board.BoardError(f'准备 Codex app-server {VERSION} 失败：{exc}') from exc
    return [str(executable), '--listen', 'stdio://', '--session-source', 'mcp']


def _install(cache, entrypoint, target, digest):
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='download-', dir=cache.parent) as directory:
        stage = Path(directory)
        archive = stage / 'release.tar.gz'
        url = f'{RELEASE_URL}/codex-app-server-package-{target}.tar.gz'
        with urlopen(url, timeout=30) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        with archive.open('rb') as data:
            if hashlib.file_digest(data, 'sha256').hexdigest() != digest:
                raise ValueError('官方发布包的 SHA-256 不匹配')
        package = stage / 'package'
        with tarfile.open(archive, 'r:gz') as artifact:
            artifact.extractall(package, filter='data')
        if not (package / entrypoint).is_file():
            raise ValueError('官方发布包缺少 app-server 入口')
        try:
            os.rename(package, cache)
        except OSError:
            # Workers sharing the same Crew home can complete the same installation concurrently.
            if not (cache / entrypoint).is_file():
                raise
