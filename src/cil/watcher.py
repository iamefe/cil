import logging
import os
import threading
import time

from cil.database import get_collection
from cil.indexer import Indexer
from cil.models import CILIndex
from cil import sqlite_db
from pathlib import Path


def _norm(p: str) -> Path:
    """Normalize a user-supplied path: expand ~ and resolve symlinks."""
    return Path(p).expanduser().resolve(strict=False)


DEBOUNCE_SECONDS = 2


def _is_within_project(filepath: str, project_path: str) -> bool:
    """Check that a resolved filepath is within the project directory.

    Resolves symlinks and relative paths (../ escapes) so an attacker cannot
    place a symlink inside the watched tree pointing outside it.
    """
    real = os.path.realpath(filepath)
    base = os.path.realpath(project_path)
    return real.startswith(base + os.sep) or real == base


class FileWatcher:
    """Watch a project directory for file changes and re-index incrementally."""

    def __init__(self, project_path: str, enrich: bool = False, use_sqlite: bool = False):
        self.project_path = str(_norm(project_path))
        self.enrich = enrich
        self.use_sqlite = use_sqlite
        self.running = False
        self._timer = None
        self._lock = threading.Lock()

    def start(self):
        """Start watching for file changes."""
        # Validate path exists
        if not os.path.exists(self.project_path):
            print(f"Error: Path does not exist: {self.project_path}")
            sqlite_db.mark_path_invalid(self.project_path, "Path does not exist")
            return

        # Validate in watch database
        if not sqlite_db.is_path_valid(self.project_path):
            print(f"Error: Path is not valid in watch database: {self.project_path}")
            return

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            print("watchdog not installed. Install with: pip install watchdog")
            return

        self.running = True
        handler = _make_handler(self)
        observer = Observer()
        # SECURITY: refuse to follow symlinks — prevents symlink-based data exfiltration
        observer.schedule(handler, self.project_path, recursive=True,
                          follow_links=False)
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
        if self.use_sqlite:
            self._do_reindex_sqlite()
        else:
            self._do_reindex_mongodb()

    def _do_reindex_sqlite(self):
        """Perform incremental re-index using SQLite."""
        previous_index = sqlite_db.load_index(self.project_path)
        if not previous_index:
            print(f"No index for {self.project_path}, skipping watch re-index")
            return

        indexer = Indexer()
        cil_index = indexer.index_directory(
            self.project_path,
            enrich=self.enrich,
            incremental=True,
            previous_index=previous_index,
        )

        sqlite_db.store_index(cil_index)
        print(f"Re-indexed {cil_index.project_path} ({len(cil_index.file_indices)} files)")

    def _do_reindex_mongodb(self):
        """Perform incremental re-index using MongoDB."""
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

        def _safe_event(self, event):
            """Filter out symlinks that point outside the project directory."""
            if event.is_directory:
                return False
            filepath = event.src_path
            if not _is_within_project(filepath, self.watcher.project_path):
                logging.warning(
                    "Watchdog: skipped file outside project — %s (resolved to %s)",
                    filepath,
                    os.path.realpath(filepath),
                )
                return False
            return True

        def on_modified(self, event):
            if self._safe_event(event):
                self.watcher._schedule_reindex()

        def on_created(self, event):
            if self._safe_event(event):
                self.watcher._schedule_reindex()

        def on_deleted(self, event):
            if self._safe_event(event):
                self.watcher._schedule_reindex()

    return _FileChangeHandler(watcher)