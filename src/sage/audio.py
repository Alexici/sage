"""Audio output: a real sound card sink, and a silent one that only keeps the timing."""
import sys
import threading
import time

import numpy as np


# --------------------------------------------------------------------------- audio output
class SoundSink:
    def __init__(self, sr: int):
        import sounddevice as sd
        self.stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
        self.stream.start()

    def write(self, a: np.ndarray, alive=None):
        self.stream.write(np.ascontiguousarray(a.reshape(-1, 1)))


class NullSink:
    """Pretends to play (sleeps for the clip length) so timing stats stay realistic."""

    def __init__(self, sr: int):
        self.sr = sr

    def write(self, a: np.ndarray, alive=None):
        time.sleep(len(a) / self.sr)


def make_sink(sr: int, no_audio: bool):
    if no_audio:
        return NullSink(sr)
    try:
        return SoundSink(sr)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Could not open the audio device ({exc}). Install portaudio and sounddevice, "
                 f"or run with --no-audio.")


# --------------------------------------------------------------------------- audio to network clients
def to_pcm16(a: np.ndarray) -> bytes:
    """Float samples (-1..1) to little-endian signed 16-bit bytes, the wire format of the audio messages."""
    return (np.clip(np.asarray(a, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class RouterSink:
    """The sink the voice engine writes to. Speech goes to the clients that asked for it (the hub sends it as
    binary frames); with none connected it plays on the local sink, so the terminal client and a PC without the app
    behave as before.

    A network client has no sound card to block us, so this paces itself like one: write() waits until at most LEAD
    seconds of audio are ahead of the clock. The voice engine's timing (holds, gaps, fillers) assumes exactly that.
    flush() drops what is queued and tells the clients to stop (Stop pressed, a reply cancelled)."""
    LEAD = 0.3

    def __init__(self, sr: int, local):
        self.sr, self.local = sr, local
        self.hub = None                  # set by the server; needs wants_audio(), send_audio(bytes, valid), flush_audio()
        self._ahead_until = 0.0
        self._epoch = 0
        self._lock = threading.Lock()

    def write(self, a: np.ndarray, alive=None):
        """`alive()` says whether the reply this chunk belongs to is still current. It is asked again on the hub's
        thread, after any Stop: a chunk whose write() began just after a cancel must not become a stray blip."""
        hub = self.hub
        if hub is None or not hub.wants_audio():
            self.local.write(a)
            return
        epoch = self._epoch
        with self._lock:
            now = time.monotonic()
            if self._ahead_until < now:
                self._ahead_until = now              # the client's buffer ran dry: it is not ahead of anything
        while self._ahead_until - time.monotonic() > self.LEAD:
            if epoch != self._epoch:
                return                                # flushed while waiting: this chunk belongs to a dead reply
            time.sleep(0.01)
        if epoch != self._epoch or (alive is not None and not alive()):
            return
        hub.send_audio(to_pcm16(a), lambda: epoch == self._epoch and (alive is None or alive()))   # and again on the hub's thread
        with self._lock:
            self._ahead_until += len(a) / self.sr

    def flush(self):
        with self._lock:
            self._epoch += 1
            self._ahead_until = 0.0
        hub = self.hub
        if hub is not None:
            hub.flush_audio()
