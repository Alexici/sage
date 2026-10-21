"""Speech to text. faster-whisper on the CPU (it has no AMD GPU backend). Push-to-talk is turn based, so
Chatterbox is idle while Whisper runs and the two never compete for the CPU."""
import numpy as np

SR = 16000


class WhisperTranscriber:
    def __init__(self, model: str, language: str, threads: int, download_root, hint: str):
        from faster_whisper import WhisperModel
        self.language = None if language == "auto" or model.endswith(".en") else language
        self.hint = hint
        self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads,
                                  download_root=str(download_root))
        self(np.zeros(SR, dtype=np.float32))   # warm-up so the first real call is not slow

    def __call__(self, audio: np.ndarray) -> str:
        # vad_filter drops silence (the main source of invented "Thank you." text); no previous-text
        # conditioning keeps one bad guess from leaking into the next; the hint spells the names right.
        segments, _info = self.model.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=self.hint, without_timestamps=True)
        return " ".join(s.text.strip() for s in segments).strip()


class FakeTranscriber:
    """For testing the plumbing without a model."""

    def __call__(self, audio: np.ndarray) -> str:
        return "this is a fake transcript"


def make_transcriber(model: str, language: str, threads: int, download_root, hint: str):
    if model == "fake":
        return FakeTranscriber()
    return WhisperTranscriber(model, language, threads, download_root, hint)