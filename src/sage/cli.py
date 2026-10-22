"""Streaming voice-out chat: text or speech in, spoken replies out.

Pipeline (each stage runs in its own thread, so they overlap):

  you type or talk -> llama-server (streaming) -> sentence chunker -> TTS worker -> audio player

  * The LLM streams tokens. The chunker cuts them into speakable pieces (sentence ends, plus an early
    cut at the first comma of a long sentence) so speech starts before the reply is finished.
  * The TTS worker turns each chunk into audio. Chatterbox Nano runs about 2x real time on your CPU,
    so it stays ahead of playback after the first chunk.
  * The model can call tools (see tools.py), each with a permission tier: harmless ones run, anything that
    changes or deletes asks you "allow? [y/N]" first, dangerous commands are refused. A tool that runs longer than --tool-filler-delay gets a
    short filler clip from a set that fits the tool ("Let me look that up."), then a natural pause
    (--filler-pause) before the real answer. Plain chat gets a waiting-filler ("Hmm.") only when speech is still far away (--chat-filler-delay, 0 = off).
  * Reasoning/"thinking" is switched off per request and any reasoning field is never spoken.
  * Code blocks and markdown are stripped before speech; the full text still prints on screen.
  * Speech input is push-to-talk: Enter starts recording, Enter stops, faster-whisper turns it into text
    and it goes through the same path as a typed message.

Setup (inside your chatterbox venv):
  sudo pacman -S portaudio            # needed by sounddevice
  uv pip install requests sounddevice soundfile numpy faster-whisper   # faster-whisper only for speech input

Run from the repo folder (llama-server must already be running on port 8080):
  PYTHONPATH=src python -m sage --ref assets/voices/shadowheart.wav --name Sage
  PYTHONPATH=src python -m sage --ptt --ref assets/voices/shadowheart.wav --name Sage   # talk with Enter
  PYTHONPATH=src python -m sage --backend kokoro --voice af_heart
  PYTHONPATH=src python -m sage --backend pocket --ref assets/voices/shadowheart.wav --threads 2   # own venv, see plan
  PYTHONPATH=src python -m sage --backend fake --no-audio     # tests the plumbing without any TTS model

Commands inside the chat:  /talk         one spoken turn (Enter starts recording, Enter stops it)
                           /mics         list microphones (pick one with --mic)
                           /say <text>   speak text without the LLM (tests the voice)
                           /filler       play a random filler clip
                           /reset        forget the conversation
                           /quit         exit
With --ptt an empty Enter starts a spoken turn, so you talk to Sage and type only when you want to.
Ctrl+C while a reply is playing stops it. Ctrl+D or /quit exits.

Module map: cli (this file, the terminal client) -> session (conversation + history) -> chat (one turn) ->
llm (server stream), text (chunker), voice (engine), tts (backends), audio (sinks), fillers (clips),
tools (what the model can call), stt (microphone and speech recognition).
The engine never prints: it emits events (events.py) and terminal.py draws them. console = output hygiene.
"""
import argparse
import sys
from pathlib import Path

from sage import console   # first: sets TQDM_DISABLE before any TTS library loads
from sage.audio import RouterSink, make_sink
from sage.chat import make_chunker
from sage.fillers import FILLER_SETS, ensure_fillers
from sage.paths import ROOT
from sage.session import Session
from sage.terminal import confirm_listener, render
from sage.tools import DEFAULT_TOOLS, build_toolbox
from sage.tts import make_backend, ref_path
from sage.voice import Voice


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8080/v1/chat/completions")
    ap.add_argument("--model", default="local", help="llama-server ignores this; any string works")
    ap.add_argument("--backend", choices=["chatterbox", "kokoro", "pocket", "fake"],
                    default="chatterbox")
    ap.add_argument("--ref", type=ref_path, help="reference wav for Chatterbox or Pocket cloning (10-20 s, clean); "
                    "'none' means no reference (a built-in Pocket voice, see --voice)")
    ap.add_argument("--voice", default="af_heart", help="Kokoro voice name, or a built-in Pocket voice (alba, ...) when no --ref is given")
    ap.add_argument("--threads", type=int, default=8, help="torch CPU threads for TTS")
    ap.add_argument("--tts-device", default="cpu", metavar="cpu|cuda",
                    help="Chatterbox only: run it on the GPU ('cuda' also means an AMD GPU with a ROCm build of "
                         "PyTorch). Costs VRAM next to the language model; unverified on this machine"
                    )
    ap.add_argument("--pocket-quantize", action="store_true",
                    help="Pocket only: int8 weights (the authors report about 27%% faster, same quality; unverified here)")
    ap.add_argument("--name", default="Sage", help="assistant name, fills {name} in the prompt")
    ap.add_argument("--user", default="Alex", help="your name, fills {user} in the prompt")
    ap.add_argument("--prompt", type=Path, default=ROOT / "prompts" / "sarcastic_companion.txt")
    ap.add_argument("--max-tokens", type=int, default=300)
    ap.add_argument("--thinking", action="store_true", help="allow model reasoning (slower; never spoken)")
    ap.add_argument("--first-cut-words", type=int, default=6,
                    help="min words before cutting the first chunk at a comma (lower = faster start, more gaps)")
    ap.add_argument("--max-hold", type=float, default=2.0,
                    help="longest wait (s) before speech starts so the TTS can stay ahead and avoid gaps (0 = off)")
    ap.add_argument("--gap-tolerance", type=float, default=0.3,
                    help="predicted gaps shorter than this (s) are not worth waiting for")
    ap.add_argument("--filler-pause", type=float, default=1.0,
                    help="seconds of silence between a filler clip and the real answer (varies by +-20%%)")
    ap.add_argument("--wait-filler-pause", type=float, default=0.4,
                    help="seconds of silence after a waiting-filler ('Hmm.') before the answer (tool fillers use --filler-pause)")
    ap.add_argument("--tool-filler-delay", type=float, default=0.7,
                    help="a tool that runs longer than this (s) gets a filler clip (0 = off)")
    ap.add_argument("--chat-filler-delay", type=float, default=1.0,
                    help="seconds without speech before a waiting-filler ('Hmm.') in plain chat; it only plays "
                         "if speech is still estimated to be --filler-min-wait away (0 = off)")
    ap.add_argument("--post-tool-filler-delay", type=float, default=1.0,
                    help="after a tool finishes, seconds the model may take to start answering before a "
                         "filler plays (needs --chat-filler-delay above 0)")
    ap.add_argument("--filler-min-wait", type=float, default=1.2,
                    help="a waiting-filler only plays if speech is estimated to be at least this far away (s)")
    ap.add_argument("--tools", default=DEFAULT_TOOLS,
                    help="comma separated tool names, 'all' (adds run_command and the ask_coder placeholder) or "
                         "'none'. Tools that change things ask you y/N first; run_command is off by default")
    ap.add_argument("--confirm-timeout", type=float, default=120.0,
                    help="seconds to wait for your y/N on a tool confirmation before it counts as no")
    ap.add_argument("--files-root", type=Path, default=ROOT / "workspace",
                    help="the only folder the file tools can see")
    ap.add_argument("--max-tool-rounds", type=int, default=3)
    ap.add_argument("--max-history", type=int, default=16,
                    help="most messages of history kept (besides system); past this the oldest are dropped")
    ap.add_argument("--trim-to", type=int, default=None,
                    help="how many messages to keep when trimming (default: half of --max-history). Trimming in big "
                         "steps keeps llama-server's prompt cache valid for several turns in a row")
    ap.add_argument("--ptt", action="store_true", help="push-to-talk: an empty Enter starts a spoken turn")
    ap.add_argument("--mic", default=None,
                    help="microphone number or name part (see /mics); default = system default")
    ap.add_argument("--stt-model", default="small.en",
                    help="faster-whisper model (tiny.en, base.en, small.en, distil-large-v3, ...) or 'fake'")
    ap.add_argument("--stt-lang", default="en", help="speech language code, or 'auto' (ignored by .en models)")
    ap.add_argument("--stt-threads", type=int, default=8, help="CPU threads for Whisper")
    ap.add_argument("--no-audio", action="store_true", help="do not play sound (timings still simulated)")
    return ap


def make_listener(args, hold: bool = False):
    """Loads Whisper and the microphone code (only when speech input is used). hold=True gives the non-blocking
    kind for a client with a talk button (the network server); the default is the terminal's Enter-to-record."""
    from sage.stt.listen import HoldToTalk, PushToTalk, Recorder
    from sage.stt.transcribe import make_transcriber
    print(f"Loading speech recognition: {args.stt_model} ...")
    hint = f"{args.name}, {args.user}."   # spelling hint so Whisper writes the names right
    transcriber = make_transcriber(args.stt_model, args.stt_lang, args.stt_threads,
                                   ROOT.parent / "models" / "whisper", hint)
    return (HoldToTalk if hold else PushToTalk)(Recorder(args.mic), transcriber)


def repl(args, voice, toolbox, system: str, listener=None):
    session = Session(args, voice, toolbox, system)
    session.bus.subscribe(render)
    session.bus.subscribe(confirm_listener(session))
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        source = "text"
        if line == "/mics":
            from sage.stt.listen import list_mics
            print(list_mics())
            continue
        if line == "/talk" or (not line and args.ptt):
            listener = listener or make_listener(args)
            line = listener.listen()
            if not line:
                continue
            source = "voice"
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        if line == "/reset":
            session.reset()
            continue
        if line.startswith("/filler"):
            cat = line[7:].strip() or "generic"
            if cat not in voice.fillers:
                print(f"[sets: {', '.join(voice.fillers)}]")
                continue
            gen = voice.new_reply()
            voice.play_filler(gen, cat)
            voice.q_audio.put((gen, "end", None))
            voice.wait_done()
            continue
        if line.startswith("/say "):
            gen = voice.new_reply()
            ch = make_chunker(args)
            for c in ch.feed(line[5:]) + ch.flush():
                voice.say(gen, c)
            voice.end(gen)
            try:
                voice.wait_done()
            except KeyboardInterrupt:
                voice.cancel()
            continue
        session.send(line, source)


def build_engine(args):
    """Loads everything the assistant needs: system prompt, tools, TTS and the voice engine. Returns
    (voice, toolbox, system). Shared by the terminal client and the network server."""
    if not args.prompt.exists():
        sys.exit(f"System prompt file not found: {args.prompt}")
    system = args.prompt.read_text().replace("{name}", args.name).replace("{user}", args.user).strip()
    toolbox = build_toolbox(args.tools, args.files_root)

    console.install()
    print(f"Loading TTS backend: {args.backend} ...")
    backend = make_backend(args.backend, args.ref, args.voice, args.threads, args.pocket_quantize,
                           args.tts_device)
    voice_id = getattr(backend, "voice_id", "default")
    sink = RouterSink(backend.sr, make_sink(backend.sr, args.no_audio))

    print("Preparing filler clips ...")
    voice = Voice(backend, sink, filler_pause=args.filler_pause, max_hold=args.max_hold,
                  gap_tolerance=args.gap_tolerance, min_filler_wait=args.filler_min_wait,
                  wait_filler_pause=args.wait_filler_pause)
    voice.fillers = ensure_fillers(backend, voice_id, ROOT / "data" / "fillers", FILLER_SETS)
    for cat, clips in voice.fillers.items():   # clip lengths decide which waiting-filler fits a wait
        print(f"  fillers [{cat}]: " + ", ".join(f"{len(c) / backend.sr:.1f}s" for c in clips))
    return voice, toolbox, system


def main():
    args = build_parser().parse_args()
    voice, toolbox, system = build_engine(args)
    listener = make_listener(args) if args.ptt else None
    tool_names = ", ".join(toolbox.tools) if toolbox else "none"
    print(f"Ready. Talking to {args.url}. Tools: {tool_names}.")
    if args.ptt:
        print("Press Enter on an empty line to talk (Enter again to stop). Or type a message, /talk, /mics, "
              "/say <text>, /filler [set], /reset, /quit.\n")
    else:
        print("Type a message, /talk, /mics, /say <text>, /filler [set], /reset, /quit.\n")
    repl(args, voice, toolbox, system, listener)


if __name__ == "__main__":
    main()
