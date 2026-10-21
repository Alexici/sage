"""TTS backends (Chatterbox, Kokoro, Pocket, a fake one for testing). Each has name, sr, supports_tags,
thread_init() and synth(text) -> float32 numpy array."""
import re
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from sage.text import n_words


# --------------------------------------------------------------------------- TTS backends
class FakeBackend:
    """No model: a short tone per word, with a delay like a real TTS. For testing the plumbing."""
    name, supports_tags, sr = "fake", True, 24000

    def thread_init(self):
        pass

    def synth(self, text: str) -> np.ndarray:
        dur = 0.32 * max(1, n_words(text))
        time.sleep(0.4 + 0.45 * dur)
        t = np.arange(int(self.sr * dur)) / self.sr
        return (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


class ChatterboxBackend:
    name, supports_tags = "chatterbox", True

    def __init__(self, ref_wav: Path, threads: int, device: str = "cpu"):
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        if device != "cpu" and not torch.cuda.is_available():
            # AMD GPUs show up as "cuda" in a ROCm build of PyTorch; a plain (CPU) build has no GPU at all.
            sys.exit(f"--tts-device {device}: this PyTorch cannot see a GPU (torch.cuda.is_available() is False). "
                     "It needs a ROCm build of PyTorch in the same venv; check with: "
                     "python -c 'import torch; print(torch.cuda.is_available(), torch.version.hip)'")
        self.torch, self.threads, self.device = torch, threads, device
        torch.set_num_threads(threads)
        self.voice_id = ref_wav.stem
        self.model = ChatterboxTurboTTS.from_pretrained(device=device, nano=True)
        self.model.prepare_conditionals(str(ref_wav))   # processed once, not on every sentence
        self.sr = self.model.sr

    def thread_init(self):
        self.torch.set_num_threads(self.threads)

    def synth(self, text: str) -> np.ndarray:
        wav = self.model.generate(text)                 # uses the cached voice
        return wav.detach().cpu().squeeze().numpy().astype(np.float32)


class KokoroBackend:
    name, supports_tags, sr = "kokoro", False, 24000

    def __init__(self, voice: str, threads: int):
        import torch
        from kokoro import KPipeline

        self.torch, self.threads, self.voice_id = torch, threads, voice
        torch.set_num_threads(threads)
        lang = "b" if voice.startswith("b") else "a"
        self.pipe = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", device="cpu")
        self.voice = voice

    def thread_init(self):
        self.torch.set_num_threads(self.threads)

    def synth(self, text: str) -> np.ndarray:
        parts = []
        for _, _, audio in self.pipe(text, voice=self.voice, speed=1.0):
            a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            parts.append(a.astype(np.float32))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def is_plain_pcm16_wav(path: Path) -> bool:
    """True if Python's stdlib `wave` can read it and it is 16-bit: the only kind Pocket's loader accepts."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getsampwidth() == 2
    except (wave.Error, EOFError):
        return False


def to_pcm16_mono_wav(src: Path, dst: Path) -> None:
    """Rewrites any audio file libsndfile understands (WAV float/24-bit/RF64, FLAC, OGG, MP3 ...) as 16-bit mono WAV."""
    import soundfile as sf
    data, sr = sf.read(str(src), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


REMOTE_PREFIXES = ("hf://", "http://", "https://")


def is_remote(ref) -> bool:
    return isinstance(ref, str) and ref.startswith(REMOTE_PREFIXES)


def fetch_hf(ref: str) -> Path:
    """Downloads (or finds in the Hugging Face cache) hf://owner/repo/path/in/repo[@revision] and returns the file."""
    spec, rev = ref[len("hf://"):], None
    if "@" in spec:
        spec, rev = spec.rsplit("@", 1)
    parts = spec.split("/")
    if len(parts) < 3 or not all(parts):
        raise ValueError(f"{ref!r} is not a file path: it looks like hf://owner/repo/folder/file.wav")
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo_id="/".join(parts[:2]), filename="/".join(parts[2:]), revision=rev))


def voice_label(ref, voice: str) -> str:
    """A short, file-name-safe name for the voice: it names the filler-clip cache folder. A local WAV keeps its
    stem ("shadowheart"); a remote or precomputed voice uses the first part of its file name."""
    if not ref:
        return voice
    if not is_remote(ref) and Path(ref).suffix == ".wav":
        return Path(ref).stem
    base = str(ref).rstrip("/").rsplit("/", 1)[-1].split(".")[0]
    return re.sub(r"[^A-Za-z0-9_-]", "_", base) or "voice"


class PocketBackend:
    """Kyutai Pocket TTS: a 100M-parameter CPU model that clones a voice from a reference. No expression tags.
    It lives in its OWN venv: it needs numpy>=2, while Chatterbox pins numpy<2 on Python 3.12.

    The reference can be a local WAV, a hf://owner/repo/file path (for example one of Kyutai's published voices
    in kyutai/tts-voices), a precomputed .safetensors voice, or a plain name of a built-in voice (alba, ...).
    Pocket's loader reads WAV files with the stdlib `wave` module, so anything but a plain 16-bit PCM WAV is
    converted to a temporary one first (your original file is never touched)."""
    name, supports_tags = "pocket", False

    def __init__(self, ref: "Path | str | None", voice: str, threads: int, quantize: bool = False):
        import torch
        from pocket_tts import TTSModel   # sets torch to 1 thread on import: we set ours right after

        self.torch, self.threads = torch, threads
        torch.set_num_threads(threads)
        self.model = TTSModel.load_model(quantize=quantize)
        self.sr = self.model.sample_rate
        self.voice_id = voice_label(ref, voice)
        try:
            self.state = self._state_for(ref) if ref else self.model.get_state_for_audio_prompt(voice)
        except SystemExit:
            raise
        except Exception as exc:   # noqa: BLE001 - gated weights, no network, a bad file: one line, not a traceback
            sys.exit(f"Pocket TTS: could not load the voice {str(ref or voice)!r}: {exc}")

    def _state_for(self, ref):
        if isinstance(ref, str) and ref.startswith("hf://"):
            ref = fetch_hf(ref)                              # then handled like a local file below
        elif is_remote(ref):
            return self.model.get_state_for_audio_prompt(ref)    # an https URL: Pocket downloads it itself
        if ref.suffix == ".safetensors":                      # a precomputed voice: no audio, no conversion
            return self.model.get_state_for_audio_prompt(str(ref))
        if is_plain_pcm16_wav(ref):
            return self.model.get_state_for_audio_prompt(str(ref))
        with tempfile.TemporaryDirectory(prefix="sage-pocket-") as tmp:
            fixed = Path(tmp) / (ref.stem + ".wav")
            try:
                to_pcm16_mono_wav(ref, fixed)
            except Exception as exc:   # soundfile missing, or it cannot decode this file
                sys.exit(f"Pocket TTS: cannot read {ref} ({exc}). Convert it once with: ffmpeg -i {ref} "
                         f"-ac 1 -ar 24000 -c:a pcm_s16le {ref.with_name(ref.stem + '_pocket.wav')}")
            return self.model.get_state_for_audio_prompt(str(fixed))

    def thread_init(self):
        self.torch.set_num_threads(self.threads)

    def synth(self, text: str) -> np.ndarray:
        audio = self.model.generate_audio(self.state, text)     # not thread-safe: one TTS thread, as we have
        return audio.detach().cpu().squeeze().numpy().astype(np.float32)


def ref_path(value: str) -> "Path | str | None":
    """argparse type for --ref: a path, a hf:// or https:// address (Pocket only), or the word "none" to use a
    built-in voice instead (the last --ref wins, so `--ref none` at the end of a command overrides a --ref baked
    into a shell function)."""
    value = value.strip()
    if value.lower() in ("none", ""):
        return None
    return value if is_remote(value) else Path(value)


KOKORO_DEFAULT_VOICE = "af_heart"    # the CLI default for --voice; meaningless for Pocket
POCKET_DEFAULT_VOICE = "alba"


def make_backend(name: str, ref, voice: str, threads: int, quantize: bool = False, device: str = "cpu"):
    if device != "cpu" and name != "chatterbox":
        sys.exit(f"--tts-device {device} only works with --backend chatterbox (Kokoro and Pocket are made for the CPU)")
    if name == "chatterbox":
        if not ref or is_remote(ref) or not Path(ref).exists():
            sys.exit("Chatterbox needs --ref path/to/reference.wav, a file on this machine (10-20 s of clean "
                     "single-speaker audio); hf:// and https:// references only work with --backend pocket")
        return ChatterboxBackend(Path(ref), threads, device)
    if name == "kokoro":
        return KokoroBackend(voice, threads)
    if name == "pocket":
        if ref and not is_remote(ref) and not Path(ref).exists():
            sys.exit(f"Reference not found: {ref}")
        return PocketBackend((ref if is_remote(ref) else Path(ref)) if ref else None,
                             POCKET_DEFAULT_VOICE if voice == KOKORO_DEFAULT_VOICE else voice, threads, quantize)
    return FakeBackend()
