from __future__ import annotations

import sys
import threading
import time


class Spinner:
    """Animasi loader di terminal — proses masih berjalan."""

    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, message: str):
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = time.time()

    def __enter__(self):
        self._start = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop.set()
        if self._thread:
            self._thread.join()
        self._clear_line()
        return False

    def update(self, message: str) -> None:
        self.message = message

    def _clear_line(self) -> None:
        sys.stdout.write("\r" + " " * 120 + "\r")
        sys.stdout.flush()

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            elapsed = int(time.time() - self._start)
            sys.stdout.write(f"\r  {frame} {self.message} ({elapsed}s)")
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)


def print_step(step: int, total: int, label: str) -> None:
    """Header tahap migrasi, mis. [1/3] SUPPLIER."""
    bar_total = max(total, 1)
    width = 20
    filled = int(width * step / bar_total)
    bar = "█" * filled + "░" * (width - filled)
    print(f"\n  [{bar}] Langkah {step}/{total} — {label.upper()}")


def show_progress(current: int, total: int, label: str = "") -> None:
    if total <= 0:
        return
    pct = min(100.0, (current / total) * 100)
    width = 32
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    suffix = f" — {label}" if label else ""
    sys.stdout.write(f"\r  [{bar}] {pct:5.1f}% ({current:,}/{total:,}){suffix}  ")
    sys.stdout.flush()


def finish_progress() -> None:
    sys.stdout.write("\n")
    sys.stdout.flush()
