"""Keeps the TTS model's console noise (progress bars, debug prints) off the screen.

Import this module before any TTS library: it switches tqdm off through the environment.
Call install() once at start-up to wrap stdout/stderr."""
import contextlib
import os
import sys
import threading

os.environ.setdefault("TQDM_DISABLE", "1")   # Chatterbox draws progress bars; they garble the printed reply

# --------------------------------------------------------------------------- keep TTS noise off the screen
_MUTED = set()          # thread ids whose console output is dropped while they synthesize
REAL_STDERR = sys.stderr


class _ThreadFilter:
    """Wraps stdout/stderr. Output from muted threads (the TTS model printing 'S3 Token -> Mel Inference...',
    progress bars) is dropped, so the reply text stays clean under your prompt."""

    def __init__(self, real):
        self._real = real

    def write(self, s):
        if threading.get_ident() in _MUTED:
            return len(s)
        return self._real.write(s)

    def flush(self):
        if threading.get_ident() not in _MUTED:
            self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextlib.contextmanager
def quiet():
    tid = threading.get_ident()
    _MUTED.add(tid)
    try:
        yield
    finally:
        _MUTED.discard(tid)


def silence_tqdm():
    """Belt and braces next to TQDM_DISABLE: force every tqdm bar off, whatever version is installed."""
    try:
        import tqdm.std as std
        orig = std.tqdm.__init__

        def init(self, *a, **k):
            k["disable"] = True
            orig(self, *a, **k)

        std.tqdm.__init__ = init
    except Exception:  # noqa: BLE001
        pass


def install():
    """Wrap stdout/stderr so muted threads (see quiet()) print nothing, and force tqdm off."""
    sys.stdout, sys.stderr = _ThreadFilter(sys.stdout), _ThreadFilter(sys.stderr)
    silence_tqdm()
