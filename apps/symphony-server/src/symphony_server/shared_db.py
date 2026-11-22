"""One SQLite connection shared by the server's threads, and closing it while they may still be using it.

The vault index and the calendar each keep one connection that the event loop and worker threads (a vault sync, the
file watcher's reindex, an .ics import) share under a lock. Closing it is the risky part: closing a connection while
another thread is inside a statement on it crashes the process. So `close` signals background jobs to stop, waits
for them to return, and then closes the connection while holding the same lock every transaction holds. After that
`transaction` raises `DbClosed` instead of touching SQLite, and background writers end on it without writing.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# How long `close` waits for background jobs to see the stop signal. A job blocked on a slow disk read does not
# hold the lock then, so after this the connection is closed anyway and the job's next write is refused.
STOP_WAIT_S = 5.0


class DbClosed(Exception):
    """The database is closed (the server is shutting down): nothing more is read or written."""


class SharedDb:
    """An SQLite connection used from several threads: transactions under one lock, background jobs that can be
    told to stop, and a close that waits for them and never races a statement."""

    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self.lock = threading.Lock()
        # Set by `close`: background jobs check it between their steps and return early.
        self.stopping = threading.Event()
        self.closed = False
        self.jobs = 0
        self.idle = threading.Condition()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """The connection under the lock, committed on success and rolled back on error; DbClosed once closed."""
        with self.lock:
            if self.closed:
                raise DbClosed("the database is closed")
            with self.connection:
                yield self.connection

    @contextmanager
    def job(self) -> Iterator[threading.Event]:
        """Run a background job (a sync, an import) that `close` waits for; yields the stop signal to check."""
        with self.idle:
            self.jobs += 1
        try:
            yield self.stopping
        finally:
            with self.idle:
                self.jobs -= 1
                self.idle.notify_all()

    def close(self, wait_s: float = STOP_WAIT_S) -> bool:
        """Tell background jobs to stop, wait up to `wait_s` for them to return, then close the connection under the
        lock (never during a statement). Safe to call twice. Whether every job had returned in time."""
        self.stopping.set()
        with self.idle:
            stopped = self.idle.wait_for(lambda: self.jobs == 0, timeout=wait_s)
        with self.lock:
            if not self.closed:
                self.closed = True
                self.connection.close()
        return stopped
