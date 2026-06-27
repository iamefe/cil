import os
import threading
import time

from cil.database import get_collection
from cil.indexer import Indexer
from cil.models import CILIndex

DEBOUNCE_SECONDS = 2


class FileWatcher:
    """Watch a project directory for file changes and re-index incrementally."""

    def __init__(self, project_path: str, enrich: bool = False):
        self.project_path = os.path.abspath(project_path)
        self.enrich = enrich
        self.running = False
        self._timer = None
        self._lock = threading.Lock()

    def start(self):
        """Start watching for file changes."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            print("watchdog not installed. Install with: pip install watchdog")
            return

        self.running = True
        handler = _make_handler(self)
        observer = Observer()
        observer.schedule(handler, self.project_path, recursive=True)
        observer.start()
        print(f"Watching {self.project_path} for changes...")

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
        finally:
            observer.stop()
            observer.join()

    def stop(self):
        """Stop watching."""
        self.running = False

    def _schedule_reindex(self):
        """Schedule a debounced re-index."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._do_reindex)
            self._timer.start()

    def _do_reindex(self):
        """Perform incremental re-index."""
        col = get_collection()
        doc = col.find_one({"project_path": self.project_path})
        if not doc:
            print(f"No index for {self.project_path}, skipping watch re-index")
            return

        previous_index = CILIndex(**doc)
        indexer = Indexer()
        cil_index = indexer.index_directory(
            self.project_path,
            enrich=self.enrich,
            incremental=True,
            previous_index=previous_index,
        )

        col.replace_one(
            {"project_path": cil_index.project_path},
            cil_index.model_dump(),
            upsert=True,
        )

        print(f"Re-indexed {cil_index.project_path} ({len(cil_index.file_indices)} files)")


def _make_handler(watcher: FileWatcher):
    """Create a FileSystemEventHandler class dynamically to avoid importing
    watchdog at module level (it's an optional dependency)."""
    from watchdog.events import FileSystemEventHandler

    class _FileChangeHandler(FileSystemEventHandler):
        def __init__(self, w):
            self.watcher = w

        def on_modified(self, event):
            if event.is_directory:
                return
            self.watcher._schedule_reindex()

        def on_created(self, event):
            if event.is_directory:
                return
            self.watcher._schedule_reindex()

        def on_deleted(self, event):
            if event.is_directory:
                return
            self.watcher._schedule_reindex()

    return _FileChangeHandler(watcher)