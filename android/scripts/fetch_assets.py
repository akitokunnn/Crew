"""Fetch the pinned Android resources and install only the locked archive members."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
from tempfile import NamedTemporaryFile
from urllib.request import urlopen


ANDROID_DIR = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def matches(path, digest, size=None):
    return path.is_file() and (size is None or path.stat().st_size == size) and sha256(path) == digest


def verify(path, digest, size):
    actual_size = path.stat().st_size
    actual_digest = sha256(path)
    if actual_size != size or actual_digest != digest:
        raise ValueError(
            f"{path}: expected {size} bytes / SHA-256 {digest}, "
            f"got {actual_size} bytes / SHA-256 {actual_digest}"
        )


def install(source, dest, digest, size):
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = NamedTemporaryFile(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part", delete=False)
    temporary = Path(target.name)
    try:
        with target:
            shutil.copyfileobj(source, target)
        verify(temporary, digest, size)
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


def verified_archive(root, entry):
    cached = root / ".cache" / entry["name"]
    if cached.exists():
        verify(cached, entry["sha256"], entry["bytes"])
        return cached

    print(f"Download {entry['name']}", flush=True)
    with urlopen(entry["url"], timeout=90) as source:
        install(source, cached, entry["sha256"], entry["bytes"])
    return cached


def extract_members(root, entry, dest):
    pending = {}
    for member, digest in entry["extract"].items():
        output = dest / PurePosixPath(member).name
        if not matches(output, digest):
            pending[member] = (output, digest)
    if not pending:
        return False

    cached = verified_archive(root, entry)
    with tarfile.open(cached, "r|bz2") as archive:
        for member in archive:
            if member.name not in pending:
                continue
            if not member.isfile():
                raise ValueError(f"{cached}: locked member is not a regular file: {member.name}")
            output, digest = pending.pop(member.name)
            with archive.extractfile(member) as source:
                install(source, output, digest, member.size)
            if not pending:
                return True
    raise ValueError(f"{cached}: missing locked members: {', '.join(pending)}")


def fetch(root, entry):
    dest = root / entry["dest"]
    if "extract" in entry:
        return extract_members(root, entry, dest)
    if matches(dest, entry["sha256"], entry["bytes"]):
        return False
    with verified_archive(root, entry).open("rb") as source:
        install(source, dest, entry["sha256"], entry["bytes"])
    return True


def main():
    try:
        entries = json.loads((ANDROID_DIR / "assets.lock.json").read_text(encoding="utf-8"))
        for entry in entries:
            installed = fetch(ANDROID_DIR, entry)
            print(f"{'Installed' if installed else 'Skip (verified)'} {entry['name']}", flush=True)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
