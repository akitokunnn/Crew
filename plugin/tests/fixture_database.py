"""Reuse an empty production schema; each functional test owns its database."""
from contextlib import closing
from pathlib import Path
import tempfile
import threading

from crew import store


_image = None
_lock = threading.Lock()


def seed_empty_home(home):
    global _image
    with _lock:
        if _image is None:
            with tempfile.TemporaryDirectory(prefix='crew-empty-test-schema-') as temporary:
                with closing(store.connect(temporary)) as conn:
                    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                _image = store.db_path(temporary).read_bytes()
        image = _image
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    with store.db_path(home).open('xb') as destination:
        destination.write(image)
