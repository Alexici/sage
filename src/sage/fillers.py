"""Short spoken clips ("Let me look that up.") that cover slow tools and slow replies."""
import hashlib
from pathlib import Path

from sage.console import quiet
from sage.text import clean_for_speech

# Short clips in the assistant's voice, grouped by situation. Edit freely: changed or new lines are generated
# the next time you start (clips are cached in data/fillers). "thinking" is the waiting-filler set (--chat-filler-delay).
FILLER_SETS = {
    "generic": ["Give me a sec.", "One moment.", "Hang on a second.", "Hold on, working on it."],
    "search": ["Let me look that up.", "Checking online, one sec.", "Let me see what I can find."],
    "system": ["On it, give me a second.", "Let me take a look.", "Checking that now."],
    "coder": ["Let me work on that.", "Give me a moment to think this through.", "Okay, let me dig into that."],
    "thinking": ["Hmm...", "Well...", "So...", "Emm...", "Umm..."],
}


def ensure_fillers(backend, voice_id: str, folder: Path, sets: dict) -> dict:
    """Generates any missing filler clips (once, then cached on disk) and loads them."""
    import soundfile as sf

    d = folder / f"{backend.name}_{voice_id}"
    d.mkdir(parents=True, exist_ok=True)
    backend.thread_init()
    with quiet():
        backend.synth("Warm up.")  # first call is slow; keep it out of the real timings
    out = {}
    for cat, phrases in sets.items():
        clips = []
        for phrase in phrases:
            key = hashlib.md5(phrase.encode()).hexdigest()[:8]
            p = d / f"{cat}_{key}.wav"
            if not p.exists():
                print(f"  generating filler [{cat}] {phrase}")
                with quiet():
                    audio = backend.synth(clean_for_speech(phrase, backend.supports_tags))
                sf.write(p, audio, backend.sr)
            data, sr = sf.read(p, dtype="float32")
            if sr == backend.sr:
                clips.append(data)
        out[cat] = clips
    return out
