"""Speech input: record from the microphone, then transcribe.

PushToTalk is for the terminal: a terminal cannot see a key being held down or released (and global hotkeys do
not work reliably on Wayland), so it is press Enter to start, press Enter to stop. HoldToTalk is for the app:
the client sends "start" when the button goes down and "stop" when it comes up, and nothing here blocks.
"""
import time

import numpy as np

from sage.stt.transcribe import SR

MIN_SECONDS = 0.3     # shorter than this is a mis-press
MIN_PEAK = 0.005      # quieter than this means the wrong input device or a muted mic


class Recorder:
    def __init__(self, device=None, stream_factory=None):
        self.device = int(device) if isinstance(device, str) and device.isdigit() else device
        self._factory = stream_factory      # tests inject a fake stream here
        self._frames, self._stream = [], None

    def _on_audio(self, indata, _frames, _time, _status):
        self._frames.append(indata[:, 0].copy())

    def start(self):
        self._frames = []
        if self._factory:
            self._stream = self._factory(self._on_audio)
        else:
            import sounddevice as sd
            self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                          device=self.device, callback=self._on_audio)
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)


def list_mics() -> str:
    import sounddevice as sd
    rows = [f"  {i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    return "input devices (use --mic <number or name part>):\n" + "\n".join(rows)


def check_audio(audio: np.ndarray) -> "str | None":
    """Why a recording is not worth transcribing, or None if it is."""
    if len(audio) / SR < MIN_SECONDS:
        return "too short, ignored"
    if float(np.abs(audio).max()) < MIN_PEAK:
        return "no sound picked up: wrong or muted microphone? (see --mic)"
    return None


class PushToTalk:
    def __init__(self, recorder: Recorder, transcriber):
        self.recorder, self.transcriber = recorder, transcriber

    def listen(self) -> str | None:
        """One spoken turn. Returns the text, or None if nothing usable was heard."""
        try:
            self.recorder.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[could not open the microphone: {exc}]\n[try /mics and --mic]")
            return None
        try:
            input("  recording... press Enter to stop ")
        except (EOFError, KeyboardInterrupt):
            self.recorder.stop()
            print("\n[cancelled]")
            return None
        audio = self.recorder.stop()
        secs = len(audio) / SR
        problem = check_audio(audio)
        if problem:
            print(f"[{problem}]")
            return None
        t = time.monotonic()
        text = self.transcriber(audio)
        print(f"  [stt] {secs:.1f}s of audio -> {time.monotonic() - t:.2f}s")
        if not text:
            print("[heard nothing]")
            return None
        return text


class HoldToTalk:
    """For a client with a talk button: start() opens the microphone, finish() closes it and transcribes,
    abort() closes it and throws the audio away. finish() returns (text, note): text is None when nothing usable
    was heard, and note says why in a short sentence for the user."""

    def __init__(self, recorder: Recorder, transcriber):
        self.recorder, self.transcriber = recorder, transcriber

    def start(self) -> None:
        self.recorder.start()

    def abort(self) -> None:
        self.recorder.stop()

    def finish(self) -> tuple:
        audio = self.recorder.stop()
        problem = check_audio(audio)
        if problem:
            return None, problem
        text = self.transcriber(audio)
        return (text, None) if text else (None, "heard nothing")
