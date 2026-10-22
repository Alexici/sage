"""A scripted Sage for building and testing a client without a model, a GPU or a speaker (`serve --demo`).

It is the real Session (so confirmations, Stop, timeouts and history behave exactly like the real thing) with the
language model and the voice replaced by a script. Type a keyword to see a situation:

  tool     a tool call            confirm  a confirmation (it does nothing real)    code   a code block
  long     a long reply           tags     voice tags                               error  a failed turn
  slow     a 5 s thinking pause   voice    acts out a spoken turn (also: hold-to-talk on the local client)
  sound    a test tone as audio frames (ask for playback with audio_config first); talk_start with source
           'client' counts your microphone frames and answers as if you had said something

Anything else gets a short canned reply."""
import itertools
import math
import re
import threading
import time
from types import SimpleNamespace

import numpy as np

from sage.audio import NullSink, RouterSink
from sage.session import Session
from sage.tools import CONFIRM, Tool, Toolbox

CANNED = [
    "Fascinating. I'll pretend I was listening. Try typing tool, confirm, code, long, tags, error, slow or voice.",
    "Bold of you to assume I have opinions about that. I do, obviously. They're just not flattering.",
    "Sure. Let's call that progress, since nobody else will.",
]
CODE_REPLY = ("Here's the short version. It's on screen.\n```python\ndef greet(name):\n    return f\"Hello, {name}.\"\n```\n"
              "It does what it says. Revolutionary, I know.")
LONG_REPLY = " ".join(["Honestly, this is the kind of reply that goes on long enough to test scrolling and redraws,"
                       " so I'll keep talking about nothing in particular until the layout proves itself."] * 4)
TAGS_REPLY = "[chuckle] Oh, that's rich. [sigh] Fine, I'll allow it. [laugh] Don't get used to it."
VOICE_TEXT = "Tell me something dry about Mondays."
PIECES = re.compile(r"\S+\s*|\s+")


class DemoVoice:
    """Just the part of Voice that Session uses: a generation counter that Stop bumps."""
    def __init__(self):
        self.gen = 0
        self.sink = RouterSink(24000, NullSink(24000))      # so a client can try audio playback without a model

    def new_reply(self):
        self.gen += 1
        return self.gen

    def cancel(self):
        self.gen += 1
        self.sink.flush()


class _Stopped(Exception):
    pass


def make_demo_session(confirm_timeout: float = 30.0, pace: float = 1.0) -> "DemoSession":
    names = ["get_time", "web_search", "list_files", "read_file", "write_file", "edit_file", "delete_file"]
    toolbox = Toolbox([Tool(n, "demo", {"type": "object", "properties": {}}, lambda: "demo",
                            tier=CONFIRM if n in ("write_file", "edit_file", "delete_file") else "auto")
                       for n in names])
    args = SimpleNamespace(name="Sage", user="Alex", confirm_timeout=confirm_timeout, max_history=16)
    return DemoSession(args, DemoVoice(), toolbox, "demo", pace=pace)


class DemoSession(Session):
    def __init__(self, args, voice, toolbox, system, pace: float = 1.0):
        super().__init__(args, voice, toolbox, system)
        self.pace = pace                    # 0 makes every pause instant (tests)
        self._canned = itertools.cycle(CANNED)
        self._recording = False
        self.tone_seconds = 1.5
        self.fed_bytes = 0          # microphone bytes received from a client (what the tests look at)

    # ---- local hold-to-talk (the demo has no microphone: it plays a fixed spoken turn)
    @property
    def can_talk(self) -> bool:
        return True

    @property
    def can_talk_client(self) -> bool:
        return True

    def feed_audio(self, data: bytes) -> None:
        if self._recording and self._source == "client":
            self.fed_bytes += len(data)

    def abort_talk(self, note=None) -> None:
        if self._recording:
            self._recording = False
            self.set_state("idle")

    def talk_start(self, source: str = "server") -> bool:
        if self._recording or self._hf or self.busy:
            return False
        self._recording, self._source, self.fed_bytes = True, source, 0
        self.set_state("listening")
        return True

    def talk_stop(self):
        if not self._recording:
            return None
        self._recording = False
        self.set_state("transcribing")
        self._sleep(0.8)
        return VOICE_TEXT

    # ---- helpers
    def _sleep(self, seconds: float, gen: "int | None" = None) -> None:
        end = time.monotonic() + seconds * self.pace
        while time.monotonic() < end:
            time.sleep(min(0.02, max(0.0, end - time.monotonic())))
            if gen is not None and self.voice.gen != gen:
                raise _Stopped

    def _say(self, text: str, gen: int) -> str:
        """Streams text as tokens, like the real thing (a token is a word, about 40 per second)."""
        for i, piece in enumerate(PIECES.findall(text)):
            self._sleep(0.025, gen)
            self.bus.emit("token", text=piece)
            if i == 2:
                self.set_state("speaking")
        return text

    def _tone(self, gen: int) -> None:
        """A rising sine sweep, 220 to 880 Hz, written to the sink in 0.1 s pieces like the real voice does."""
        sr, n = 24000, int(24000 * self.tone_seconds)
        t = np.arange(n) / sr
        sweep = 0.3 * np.sin(2 * math.pi * (220 * t + (880 - 220) * t * t / (2 * self.tone_seconds)))
        for i in range(0, n, 2400):
            if self.voice.gen != gen:
                raise _Stopped
            self.voice.sink.write(sweep[i:i + 2400].astype(np.float32))

    def _tool(self, gen: int, name: str, arguments: str, chars: int = 412, seconds: float = 1.2, tier="auto") -> None:
        self.bus.emit("tool_start", name=name, arguments=arguments)
        self._sleep(seconds, gen)
        self.bus.emit("tool", name=name, arguments=arguments, chars=chars, seconds=seconds, tier=tier, outcome="ran")

    # ---- one scripted turn
    def send(self, text: str, source: str = "text") -> bool:
        if not self._turn_lock.acquire(blocking=False):
            self.bus.emit("error", message="busy: a reply is still in progress", code="busy")
            return False
        try:
            gen = self.voice.new_reply()
            self.bus.emit("user", text=text, source=source)
            reply = ""
            try:
                reply = self._script(text, gen)
            except _Stopped:
                self.bus.emit("stopped")
                return True
            if reply:
                self.messages += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
                self._trim()
            return True
        finally:
            self._turn_lock.release()

    def _script(self, text: str, gen: int) -> str:
        words = set(re.findall(r"[a-z]+", text.lower()))
        reply = ""
        if "error" in words:
            self._sleep(0.4, gen)
            self.bus.emit("error", message="The language model is not reachable.", code="llm")
            return ""
        if "slow" in words:
            self._sleep(5.0, gen)
        if "voice" in words and "slow" not in words:
            self.set_state("listening")
            self._sleep(1.2, gen)
            self.set_state("transcribing")
            self._sleep(0.8, gen)
            self.bus.emit("user", text=VOICE_TEXT, source="voice")
            self.set_state("thinking")
            self._sleep(0.6, gen)
        elif "sound" in words:
            reply = self._say("A test tone, rising. It is not music.", gen)
            self._tone(gen)
        elif "tool" in words:
            self._tool(gen, "web_search", '{"query": "weather in Cluj"}')
            reply = self._say("Cold, as usual. Bring a coat, or don't, and learn something.", gen)
        elif "confirm" in words:
            args = {"path": "notes.txt", "content": "hello"}
            self.bus.emit("tool_start", name="write_file", arguments='{"path": "notes.txt", "content": "hello"}')
            tool = SimpleNamespace(name="write_file")
            if self._confirm(tool, args, "Create notes.txt (5 characters). Nothing real happens in the demo."):
                self.bus.emit("tool", name="write_file", arguments="{}", chars=24, seconds=0.0, tier="confirm",
                              outcome="ran")
                reply = self._say("Done. It didn't actually do anything, but the thought counts.", gen)
            else:
                self.bus.emit("tool", name="write_file", arguments="{}", chars=40, seconds=0.0, tier="confirm",
                              outcome="denied")
                reply = self._say("Fine. Cancelled.", gen)
        else:
            reply = self._say(CODE_REPLY if "code" in words else LONG_REPLY if "long" in words
                              else TAGS_REPLY if "tags" in words else next(self._canned), gen)
        if "voice" in words and "slow" not in words and not reply:
            reply = self._say("Mondays: proof that the universe has a sense of humor and no sense of timing.", gen)
        self.bus.emit("reply_end")
        self._sleep(0.5, gen)
        self.bus.emit("latency", first_token=0.31, first_chunk_ready=1.4, real_start=1.9, first_chunk="9w cut at 1.4s",
                      held=0.0, gaps=0.0, filler=None, tools=[])
        return reply
