"""Audio output: a real sound card sink, and a silent one that only keeps the timing."""
import sys
import time

import numpy as np


# --------------------------------------------------------------------------- audio output
class SoundSink:
    def __init__(self, sr: int):
        import sounddevice as sd
        self.stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
        self.stream.start()

    def write(self, a: np.ndarray):
        self.stream.write(np.ascontiguousarray(a.reshape(-1, 1)))


class NullSink:
    """Pretends to play (sleeps for the clip length) so timing stats stay realistic."""

    def __init__(self, sr: int):
        self.sr = sr

    def write(self, a: np.ndarray):
        time.sleep(len(a) / self.sr)


def make_sink(sr: int, no_audio: bool):
    if no_audio:
        return NullSink(sr)
    try:
        return SoundSink(sr)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Could not open the audio device ({exc}). Install portaudio and sounddevice, "
                 f"or run with --no-audio.")
