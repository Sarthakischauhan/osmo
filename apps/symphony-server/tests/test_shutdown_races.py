"""Closing the vault index and the calendar while a worker thread is still writing to them (server shutdown during a
vault sync or an .ics import) must neither crash nor write after the close.

Closing an SQLite connection while another thread is inside a statement on it can crash the interpreter, so each
loop runs in a child process: a crash fails the test instead of killing pytest."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from symphony_server.shared_db import DbClosed, SharedDb

ITERATIONS = 30

CHILD = r'''
import sys, threading, time
from pathlib import Path

from symphony_server.calendar_store import CalendarStore
from symphony_server.vault_index import VaultIndex
from symphony_server.vault_paths import Vault

kind, base, iterations = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
NOTES, EVENTS = 150, 400
ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(
    f"BEGIN:VEVENT\r\nUID:e{i}@x\r\nDTSTART:20300101T000000Z\r\nDTEND:20300101T010000Z\r\nSUMMARY:Event {i}\r\nEND:VEVENT\r\n"
    for i in range(EVENTS)) + "END:VCALENDAR\r\n").encode()
interrupted = 0
for it in range(iterations):
    here = base / str(it)
    root = here / "notes"
    root.mkdir(parents=True)
    if kind == "vault":
        for i in range(NOTES):
            (root / f"n{i}.md").write_text(f"# Note {i}\n" + "word " * 200)
        store = VaultIndex([Vault("notes", root)], here / "index.db")
        work, full = store.sync, NOTES
    else:
        store = CalendarStore(here / "osmo.db")
        work, full = (lambda: store.import_ics(ICS)), EVENTS
    done, errors = [], []

    def run():
        try:
            done.append(work())
        except BaseException as exc:
            errors.append(repr(exc))

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.005 + 0.001 * (it % 10))
    store.close()
    worker.join()
    if errors:
        sys.exit(f"iteration {it}: the writer failed: {errors[0]}")
    interrupted += done[0] < full
    # After the close, writing is a no-op: nothing raises and nothing is written.
    if work() != 0:
        sys.exit(f"iteration {it}: wrote after close")
print(f"ok {interrupted}")
'''


def run_loop(kind: str, tmp_path: Path) -> str:
    """Run the close-while-writing loop for `kind` in a child process; its last line of output."""
    child = subprocess.run([sys.executable, "-c", CHILD, kind, str(tmp_path), str(ITERATIONS)], capture_output=True, text=True, timeout=120)
    assert child.returncode == 0, f"exit {child.returncode}: {child.stderr[-2000:]}"
    return child.stdout.strip().splitlines()[-1]


def test_closing_the_vault_index_during_a_sync_never_crashes_or_writes_after(tmp_path: Path) -> None:
    outcome = run_loop("vault", tmp_path)
    # The loop really closed while the sync was writing: some syncs were cut short.
    assert outcome.startswith("ok ") and int(outcome.split()[1]) > 0, outcome


def test_closing_the_calendar_during_an_import_never_crashes_or_writes_after(tmp_path: Path) -> None:
    outcome = run_loop("calendar", tmp_path)
    assert outcome.startswith("ok ") and int(outcome.split()[1]) > 0, outcome


def test_a_job_that_does_not_stop_cannot_hang_close(tmp_path: Path) -> None:
    db = SharedDb(tmp_path / "x.db")
    release = threading.Event()

    def stuck() -> None:
        with db.job():
            release.wait(10)

    worker = threading.Thread(target=stuck)
    worker.start()
    started = time.monotonic()
    assert db.close(wait_s=0.2) is False
    assert time.monotonic() - started < 2
    with pytest.raises(DbClosed), db.transaction():
        pass
    release.set()
    worker.join()
