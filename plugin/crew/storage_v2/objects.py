"""Workspace-scoped immutable file objects, published only after byte verification."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import uuid


CHUNK_SIZE = 1024 * 1024


class ObjectIntegrityError(ValueError):
    """Object bytes disagree with their declared content address or size."""


def canonical_uuid(value):
    if not isinstance(value, str):
        raise ValueError('UUID must be canonical text')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError('Invalid UUID: ' + value) from exc
    if str(parsed) != value:
        raise ValueError('UUID must use lowercase canonical text: ' + value)
    return value


def canonical_sha256(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('SHA-256 must be 64 lowercase hexadecimal characters')
    return value


def _expected_size(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('Expected size must be a nonnegative integer')


def _comparison_path(path):
    value = str(path)
    # Windows realpath can retain the extended prefix when a missing path is
    # concurrently published. It still denotes the same drive/UNC location.
    if os.name == 'nt':
        if value.casefold().startswith('\\\\?\\unc\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return Path(value)


@dataclass(frozen=True)
class ObjectInfo:
    workspace_id: str
    sha256: str
    size_bytes: int
    path: Path


class ObjectStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _contained(self, path):
        if not _comparison_path(path.resolve()).is_relative_to(_comparison_path(self.root)):
            raise ObjectIntegrityError('Object path points outside its cache: ' + str(path))
        return path

    def path_for(self, workspace_id, sha256):
        canonical_uuid(workspace_id)
        canonical_sha256(sha256)
        return self._contained(self.root / workspace_id / sha256)

    @staticmethod
    def _inspect(stream, expected_sha256, expected_size):
        digest, size = hashlib.sha256(), 0
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if expected_size is not None and size > expected_size:
                raise ObjectIntegrityError('Object exceeds declared size')
        if expected_size is not None and size != expected_size:
            raise ObjectIntegrityError('Object size mismatch: expected %s, got %s' % (expected_size, size))
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise ObjectIntegrityError('Object SHA-256 mismatch: expected %s, got %s' % (expected_sha256, actual))
        return size

    def put_stream(self, workspace_id, stream, *, expected_sha256=None, expected_size=None):
        canonical_uuid(workspace_id)
        if expected_sha256 is not None:
            canonical_sha256(expected_sha256)
        _expected_size(expected_size)
        incoming = self._contained(self.root / '.incoming')
        incoming.mkdir(exist_ok=True)
        temporary = self._contained(incoming / (uuid.uuid4().hex + '.partial'))
        digest, size = hashlib.sha256(), 0
        try:
            with temporary.open('xb') as output:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError('Object input must be a binary stream')
                    if not chunk:
                        break
                    size += len(chunk)
                    if expected_size is not None and size > expected_size:
                        raise ObjectIntegrityError('Object exceeds declared size')
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            actual = digest.hexdigest()
            if expected_sha256 is not None and actual != expected_sha256:
                raise ObjectIntegrityError('Object SHA-256 mismatch: expected %s, got %s' % (expected_sha256, actual))
            if expected_size is not None and size != expected_size:
                raise ObjectIntegrityError('Object size mismatch: expected %s, got %s' % (expected_size, size))
            destination = self.path_for(workspace_id, actual)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._contained(destination)
            try:
                # Same-volume hard-link publication is atomic and cannot overwrite
                # an existing address. No reader can observe a partial final file.
                os.link(temporary, destination)
            except FileExistsError:
                self.verify(workspace_id, actual, expected_size=size)
            self._sync_directory(destination.parent)
            return ObjectInfo(workspace_id, actual, size, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _sync_directory(path):
        # Windows fsync flushes the file handle above; opening a directory through
        # os.open is not supported there. POSIX also persists the published link.
        if os.name != 'nt':
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def put_bytes(self, workspace_id, content, **expected):
        import io
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise TypeError('Object content must be bytes')
        return self.put_stream(workspace_id, io.BytesIO(content), **expected)

    def put_file(self, workspace_id, path, **expected):
        with Path(path).open('rb') as stream:
            return self.put_stream(workspace_id, stream, **expected)

    @contextmanager
    def open(self, workspace_id, sha256, *, expected_size=None):
        _expected_size(expected_size)
        path = self.path_for(workspace_id, sha256)
        with path.open('rb') as stream:
            self._inspect(stream, sha256, expected_size)
            stream.seek(0)
            yield stream

    def verify(self, workspace_id, sha256, *, expected_size=None):
        with self.open(workspace_id, sha256, expected_size=expected_size) as stream:
            size = os.fstat(stream.fileno()).st_size
        return ObjectInfo(workspace_id, sha256, size, self.path_for(workspace_id, sha256))
