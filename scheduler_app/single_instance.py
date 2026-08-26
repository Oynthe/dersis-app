"""Single-instance guard for DERSİS (ST-DATA-012).

Two copies of the app running at once share one ``settings/app_settings.egu``
and autosave over each other last-writer-wins: the audit lost a whole class plus
a language change that way. This module is the seam that stops it.

Built on ``QLockFile``, which is the right primitive here for a reason worth
writing down. Its staleness check asks whether the recorded PID is still running
*first* and only falls back to a file-age rule when that cannot decide, so a lock
left behind by a crashed instance is reclaimed in milliseconds rather than
locking the user out of their own timetable. That fallback is disabled here
(``setStaleLockTime(0)``): it can otherwise judge a *live* session stale merely
for being older than the age limit, and unlinking an open file succeeds on macOS,
which DERSİS also ships.

Deliberately importable without a ``QApplication`` — the guard has to be
acquired before the first-run language gate, which itself reads and writes the
settings file this exists to protect.

    lock = acquire_single_instance_lock()
    if lock is None:
        ...  # another copy is already running
"""
import os

from PyQt6.QtCore import QLockFile

from scheduler_app import storage

LOCK_FILENAME = "dersis.lock"

# How long a caller should be willing to wait for a lock left by a crashed
# instance to become reclaimable. With the age rule disabled this is effectively
# instant; the constant exists so callers and tests share one number.
STALE_RECLAIM_TIMEOUT = 10


def default_lock_path() -> str:
    """The lock's home: alongside the data it protects, under the Dersis root."""
    storage.ensure_dirs()
    return os.path.join(storage.root_dir(), LOCK_FILENAME)


class SingleInstanceLock:
    """An exclusive, process-wide claim on one DERSİS data directory.

    A second :meth:`acquire` over the same path fails even from *within* the same
    process, which is why this is not built on POSIX record locks: those are
    per-process and would happily hand the same directory to two windows in one
    interpreter.
    """

    def __init__(self, path: str | None = None):
        self.path = path or default_lock_path()
        self._lock = QLockFile(self.path)
        # Pure PID + hostname staleness; never "this lock looks old, take it".
        self._lock.setStaleLockTime(0)
        self._held = False

    # ── Acquisition ─────────────────────────────────────────────────────────

    def acquire(self) -> bool:
        """Claim the lock. False means a *live* instance already holds it.

        A failed acquisition leaves the existing lock completely untouched —
        never unlink on the failure path, or the guard becomes the race it was
        meant to prevent.
        """
        if self._held:
            return True
        if self._lock.tryLock(0):
            self._held = True
        return self._held

    def release(self) -> None:
        """Give the lock up. Idempotent, so a belt-and-braces second call is safe."""
        if self._held:
            self._lock.unlock()
            self._held = False

    def is_held(self) -> bool:
        return self._held

    def owner_pid(self) -> int | None:
        """PID of whoever holds the lock, readable from another process."""
        ok, pid, _host, _app = self._lock.getLockInfo()
        return int(pid) if ok else None

    # ── Context manager ─────────────────────────────────────────────────────

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        # Always releases, including when the body raised: a crash inside the
        # app must not leave the user locked out on the next launch.
        self.release()
        return False


def acquire_single_instance_lock(path: str | None = None):
    """Return a held :class:`SingleInstanceLock`, or None if one is running.

    Keep the returned object alive for the process lifetime — letting it be
    collected releases the lock.
    """
    lock = SingleInstanceLock(path)
    return lock if lock.acquire() else None
