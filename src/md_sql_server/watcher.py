"""Filesystem watcher for live sync of .md files.

Uses `watchdog` to monitor DOC_ROOTS for changes and automatically
reload affected files into the SQLite database.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from .config import RootConfig, get_doc_roots
from .database import reload_file

logger = logging.getLogger(__name__)


class _MdHandler(FileSystemEventHandler):
    """Watchdog handler that reloads .md files on change."""

    def __init__(self, conn: sqlite3.Connection, root_config: RootConfig):
        self.conn = conn
        self.root_config = root_config
        self._ignored_paths: dict[str, float] = {}  # path -> ignore_until timestamp
        self._lock = threading.Lock()

    def ignore_path(self, path: str, duration_sec: float = 1.0) -> None:
        """Temporarily ignore events for a path (to avoid self-triggering)."""
        with self._lock:
            self._ignored_paths[path] = time.time() + duration_sec

    def _is_ignored(self, path: str) -> bool:
        with self._lock:
            deadline = self._ignored_paths.get(path, 0)
            if time.time() < deadline:
                return True
            # Clean up expired entries
            if deadline > 0:
                del self._ignored_paths[path]
            return False

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = event.src_path.replace('\\', '/')
        if not path.endswith('.md'):
            return
        if self._is_ignored(path):
            return

        root_path = self.root_config.path
        root_alias = self.root_config.alias
        try:
            rel = str(Path(path).relative_to(root_path)).replace('\\', '/')
        except ValueError:
            return

        logger.info("Watcher: %s %s", event.event_type, rel)
        try:
            reload_file(self.conn, root_alias, rel, root_path)
        except Exception as e:
            logger.error("Watcher reload failed for %s: %s", rel, e)


class FileWatcher:
    """Manages watchdog observers for all configured doc roots."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.observer = Observer()
        self.handlers: dict[str, _MdHandler] = {}

    def start(self, roots: list[RootConfig] | None = None) -> None:
        """Start watching all configured roots."""
        if roots is None:
            roots = get_doc_roots()

        for cfg in roots:
            if not cfg.path.exists():
                logger.warning("Watcher: path not found: %s", cfg.path)
                continue
            handler = _MdHandler(self.conn, cfg)
            self.handlers[cfg.alias] = handler
            self.observer.schedule(handler, str(cfg.path), recursive=True)
            logger.info("Watcher: watching %s -> %s", cfg.alias, cfg.path)

        self.observer.start()

    def stop(self) -> None:
        """Stop all watchers."""
        self.observer.stop()
        self.observer.join()

    def ignore_path(self, root: str, file: str, duration_sec: float = 1.0) -> None:
        """Ignore changes to a specific file (used after md_change writes)."""
        handler = self.handlers.get(root)
        if handler is None:
            return
        full_path = str(handler.root_config.path / file).replace('\\', '/')
        handler.ignore_path(full_path, duration_sec)
