"""Hands-free start: a wake word (and a short follow-up window after each reply) opens the microphone by itself.

The listener owns its own microphone stream while Sage is idle. When the detector fires it records until the speaker
stops (an energy-based endpoint), then hands the audio to the session, which transcribes and answers like any spoken
turn. It stays deaf while Sage is busy (a reply is running, or a talk button holds the microphone), so Sage never
hears its own name through the speakers. Measure a model before trusting it: `python -m sage.stt.wake --help`.
"""
import argparse
import collections
from pathlib import Path
import queue
import sys
import threading
import time

import numpy as np

SR = 16000
FRAME = 1280           # samples; 80 ms, the size openWakeWord wants


# --------------------------------------------------------------------------- detectors
class OpenWakeWord:
    """openWakeWord through ONNX. `model` is a built-in name (hey_jarvis, alexa, ...) or the path of a .onnx file.
    The score of a frame is the highest score among the loaded models (one, here)."""

    def __init__(self, model: str, vad_threshold: float = 0.0):
        self.label = Path(model).stem if ("/" in str(model) or str(model).endswith(".onnx")) else str(model)
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            sys.exit(f"openWakeWord is not installed ({exc}). On Python 3.12+ its tflite-runtime dependency has no "
                     "wheel, so install it without dependencies:  uv pip install --no-deps openwakeword  &&  "
                     "uv pip install onnxruntime tqdm scipy scikit-learn requests")
        try:
            self.model = Model(wakeword_models=[model], inference_framework="onnx", vad_threshold=vad_threshold)
        except Exception as exc:   # noqa: BLE001 - missing feature models, a wrong name or a broken file
            sys.exit(f"could not load the wake word model {model!r}: {exc}\nThe feature models and the built-in "
                     "wake words are downloaded once with:  python -c \"import openwakeword; "
                     "openwakeword.utils.download_models()\"")

    def process(self, frame: np.ndarray) -> float:
        scores = self.model.predict(frame)
        return float(max(scores.values())) if scores else 0.0

    def reset(self) -> None:
        self.model.reset()


class FakeDetector:
    """For tests and dry runs: scores come from a function of the frame (default: the frame's first sample / 32767)."""

    def __init__(self, score_fn=None):
        self.score_fn = score_fn or (lambda frame: float(frame[0]) / 32767.0)
        self.resets = 0

    def process(self, frame: np.ndarray) -> float:
        return float(self.score_fn(frame))

    def reset(self) -> None:
        self.resets += 1


class WakeGate:
    """Turns a stream of scores into triggers: `patience` frames in a row at or above the threshold, then nothing for
    `cooldown` seconds (the same word must not fire twice)."""

    def __init__(self, threshold: float = 0.5, patience: int = 2, cooldown: float = 2.0):
        self.threshold, self.patience = threshold, max(1, patience)
        self.cooldown_frames = int(cooldown * SR / FRAME)
        self.run = self.quiet = 0

    def reset(self) -> None:
        self.run = self.quiet = 0

    def update(self, score: float) -> bool:
        if self.quiet > 0:
            self.quiet -= 1
            return False
        self.run = self.run + 1 if score >= self.threshold else 0
        if self.run >= self.patience:
            self.run, self.quiet = 0, self.cooldown_frames
            return True
        return False


# --------------------------------------------------------------------------- end of speech
def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0


class Endpointer:
    """Decides when a spoken request is over. Feed float frames (-1..1); returns None while recording goes on, then
    one of "done" (speech, then `silence_end` seconds of quiet), "nospeech" (nothing said within `no_speech`
    seconds) or "toolong". A frame counts as speech when it is clearly louder than the room: `ratio` times the noise
    floor, and never below `floor`."""

    def __init__(self, noise: float = 0.0, silence_end: float = 0.9, no_speech: float = 4.0, max_seconds: float = 20.0,
                 min_speech: float = 0.25, floor: float = 0.012, ratio: float = 3.0):
        self.noise, self.silence_end, self.no_speech = noise, silence_end, no_speech
        self.max_seconds, self.min_speech, self.floor, self.ratio = max_seconds, min_speech, floor, ratio
        self.t = self.speech = self.silence = 0.0

    def is_speech(self, level: float) -> bool:
        # Once speech has started, use a slightly softer hold threshold (70%) so quiet syllables,
        # slow speech, or "uh/um" hesitations do not get misclassified as silence.
        thresh = max(self.floor, self.ratio * self.noise)
        if self.speech > 0:
            thresh *= 0.7
        return level > thresh

    def feed(self, frame: np.ndarray) -> "str | None":
        dt = len(frame) / SR
        self.t += dt
        level = rms(frame)
        if self.is_speech(level):
            self.speech += dt
            self.silence = 0.0
        else:
            self.silence += dt
            # Do not let quiet syllables mid-sentence ratchet the room noise floor upward
            if self.speech == 0 or not self.noise or level < self.noise:
                self.noise = 0.9 * self.noise + 0.1 * level if self.noise else level
        if self.speech >= self.min_speech and self.silence >= self.silence_end:
            return "done"
        if self.speech < self.min_speech and self.t >= self.no_speech:
            return "nospeech"
        if self.t >= self.max_seconds:
            return "toolong"
        return None


# --------------------------------------------------------------------------- the listener
class WakeListener:
    """Runs in its own thread for the life of the server. `stream_factory(callback)` gives a started-able input stream
    delivering int16 blocks of FRAME samples (tests inject a fake; the default is sounddevice)."""

    ONSET_FRAMES = 3         # follow-up window: this many loud frames in a row count as the start of a request
    PRE_ROLL = 6             # frames kept before a follow-up onset (0.5 s), so the first word is not cut

    def __init__(self, session, detector, threshold: float = 0.5, patience: int = 2, cooldown: float = 2.0,
                 follow_up: float = 8.0, device=None, stream_factory=None, log=None,
                 silence_end: float = 0.9, min_speech: float = 0.25, floor: float = 0.012, ratio: float = 3.0):
        self.session, self.detector = session, detector
        self.gate = WakeGate(threshold, patience, cooldown)
        self.follow_up, self.device, self.factory = follow_up, device, stream_factory
        self.silence_end, self.min_speech, self.floor, self.ratio = silence_end, min_speech, floor, ratio
        self.log = log or (lambda m: print(f"[wake] {m}", file=sys.stderr))
        self.frames: queue.Queue = queue.Queue(maxsize=200)
        self.stop_event = threading.Event()
        self.thread = None
        self.triggers = 0
        self.noise = 0.0
        self._follow_until = 0.0
        self._was_busy = False
        self.enabled = True
        self.label = str(getattr(detector, "label", "Hey Sage"))
        self._finish_early = threading.Event()

    # ---- lifecycle
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="wake", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        if not self.enabled:
            self._finish_early.set()

    def finish_now(self) -> None:
        """Called when the user clicks the mic button during hands-free listening to transcribe immediately."""
        self._finish_early.set()

    def _callback(self, indata, _frames, _time, _status):
        block = np.asarray(indata)
        block = block[:, 0] if block.ndim == 2 else block
        try:
            self.frames.put_nowait(block.astype(np.int16, copy=True))
        except queue.Full:                      # the consumer is busy (a turn is running): drop the oldest audio
            try:
                self.frames.get_nowait()
                self.frames.put_nowait(block.astype(np.int16, copy=True))
            except (queue.Empty, queue.Full):
                pass

    def _open(self):
        if self.factory:
            return self.factory(self._callback)
        import sounddevice as sd
        return sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=FRAME, device=self.device,
                              callback=self._callback)

    def _run(self) -> None:
        try:
            stream = self._open()
            stream.start()
        except Exception as exc:   # noqa: BLE001 - no microphone: say so once, the rest of Sage keeps working
            self.log(f"off: could not open the microphone ({exc})")
            self.session.bus.emit("notice", level="warn", message=f"wake word is off: could not open the microphone ({exc})")
            return
        self.log("listening for the wake word")
        try:
            self._loop()
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:   # noqa: BLE001
                pass

    # ---- the loop
    def _next(self, timeout: float = 0.2):
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def _drain(self) -> None:
        while self._next(0.0) is not None:
            pass

    def _loop(self) -> None:
        pre = collections.deque(maxlen=self.PRE_ROLL)
        loud = 0
        while not self.stop_event.is_set():
            frame = self._next()
            if frame is None or len(frame) != FRAME:
                continue
            if not self.enabled:
                pre.clear(); loud = 0
                continue
            busy = self.session.busy or self.session.mic_busy
            if busy:                                    # deaf while Sage talks or another path holds the microphone
                if not self._was_busy:
                    self.detector.reset(); self.gate.reset()
                self._was_busy = True
                pre.clear(); loud = 0
                continue
            if self._was_busy:                          # a reply just ended: open the follow-up window
                self._was_busy = False
                self._drain()
                self._follow_until = time.monotonic() + self.follow_up
                continue
            f = frame.astype(np.float32) / 32768.0
            level = rms(f)
            in_follow = time.monotonic() < self._follow_until
            if self.noise == 0.0 or level < 3 * self.noise:
                self.noise = level if self.noise == 0.0 else 0.95 * self.noise + 0.05 * level
            pre.append(frame)
            loud = loud + 1 if (in_follow and level > max(self.floor, self.ratio * self.noise)) else 0
            score = self.detector.process(frame)
            if self.gate.update(score):
                self._handle("wake", [])
                pre.clear(); loud = 0
            elif loud >= self.ONSET_FRAMES:
                self._handle("follow_up", list(pre))
                pre.clear(); loud = 0

    def _handle(self, how: str, preroll: list) -> None:
        if not self.session.hands_free_begin():
            return
        self.triggers += 1
        self.log(how.replace("_", "-"))
        self.session.bus.emit("wake", source=how)
        end = Endpointer(noise=self.noise, silence_end=self.silence_end, min_speech=self.min_speech,
                         floor=self.floor, ratio=self.ratio)
        chunks = [f for f in preroll]
        for f in preroll:                               # the pre-roll already counts as speech heard
            end.feed(f.astype(np.float32) / 32768.0)
        result = None
        self._finish_early.clear()
        if how == "wake":
            self._drain()                               # audio from before the word is not part of the request
        while result is None and not self.stop_event.is_set() and self.session.mic_busy:
            if self._finish_early.is_set():
                result = "done"
                break
            frame = self._next(0.2)
            if frame is None:
                continue
            chunks.append(frame)
            result = end.feed(frame.astype(np.float32) / 32768.0)
        audio = (np.concatenate(chunks).astype(np.float32) / 32768.0) if chunks else np.zeros(0, np.float32)
        if not self.session.mic_busy:                   # Stop was pressed while listening: nothing to do
            pass
        elif result == "nospeech" or self.stop_event.is_set():
            self.session.hands_free_abort("didn't catch anything" if result == "nospeech" else None)
        else:
            self.session.hands_free_turn(audio)         # blocks through transcription, the reply and its speech
        self._drain()
        self.detector.reset(); self.gate.reset()
        self._was_busy = False
        self._follow_until = time.monotonic() + self.follow_up


# --------------------------------------------------------------------------- measuring a model
def score_clip(detector, audio: np.ndarray) -> list:
    """Per-frame scores for one clip (int16 mono 16 kHz)."""
    detector.reset()
    pad = (-len(audio)) % FRAME
    audio = np.concatenate([audio, np.zeros(pad, dtype=np.int16)]) if pad else audio
    return [detector.process(audio[i:i + FRAME]) for i in range(0, len(audio), FRAME)]


def count_triggers(scores: list, threshold: float, patience: int = 2, cooldown: float = 2.0) -> int:
    gate = WakeGate(threshold, patience, cooldown)
    return sum(1 for s in scores if gate.update(s))


def read_wav(path) -> np.ndarray:
    import wave
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SR):
            raise ValueError(f"{path}: needs 16 kHz mono 16-bit WAV (convert: ffmpeg -i in.x -ar 16000 -ac 1 out.wav)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def evaluate(detector, positives: list, negatives: list, thresholds=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
             patience: int = 2) -> str:
    """Recall on clips that contain the wake word and false accepts per hour on audio that does not. Returns a table."""
    pos = [score_clip(detector, read_wav(p)) for p in positives]
    neg, hours = [], 0.0
    for p in negatives:
        a = read_wav(p)
        hours += len(a) / SR / 3600
        neg.append(score_clip(detector, a))
    rows = ["threshold  recall (of %d)  false accepts  per hour (%.2f h of negatives)" % (len(pos), hours)]
    for t in thresholds:
        hit = sum(1 for s in pos if count_triggers(s, t, patience, 0.0) > 0)
        fa = sum(count_triggers(s, t, patience) for s in neg)
        rows.append(f"{t:>9.1f}  {hit:>6d} {hit / max(1, len(pos)):>6.0%}  {fa:>13d}  {fa / hours if hours else float('nan'):>9.2f}")
    return "\n".join(rows)


def live(detector, threshold: float, patience: int, device=None) -> None:
    """Prints a bar for every frame that scores above 0.1 and counts triggers until Ctrl-C. Leave it running next to
    the TV, music or a normal conversation to see the false accepts per hour in your own room."""
    import sounddevice as sd
    q: queue.Queue = queue.Queue()
    gate, t0, n = WakeGate(threshold, patience), time.monotonic(), 0
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=FRAME, device=device,
                        callback=lambda d, *_: q.put(d[:, 0].copy())):
        print(f"listening (threshold {threshold}, patience {patience}); Ctrl-C to stop")
        try:
            while True:
                s = detector.process(q.get())
                if s > 0.1:
                    print(f"{'#' * int(s * 40):<40} {s:.2f}")
                if gate.update(s):
                    n += 1
                    print(f"*** TRIGGER {n} at {(time.monotonic() - t0) / 60:.1f} min")
        except KeyboardInterrupt:
            hours = (time.monotonic() - t0) / 3600
            print(f"\n{n} triggers in {hours * 60:.1f} min = {n / hours if hours else 0:.1f} per hour")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Measure a wake word model (needs openWakeWord installed).")
    ap.add_argument("mode", choices=["live", "files"])
    ap.add_argument("--model", default="hey_jarvis", help="built-in name or path of an .onnx file")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--mic", default=None)
    ap.add_argument("--positives", nargs="*", default=[], help="WAV files (16 kHz mono) that contain the wake word")
    ap.add_argument("--negatives", nargs="*", default=[], help="WAV files that contain no wake word")
    a = ap.parse_args(argv)
    det = OpenWakeWord(a.model)
    if a.mode == "live":
        live(det, a.threshold, a.patience, int(a.mic) if a.mic and a.mic.isdigit() else a.mic)
    else:
        print(evaluate(det, a.positives, a.negatives, patience=a.patience))


if __name__ == "__main__":
    main()