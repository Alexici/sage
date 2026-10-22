"""A Session is one conversation: the message history, the trimming rules, and one turn at a time.

Clients (terminal, web UI) call send() / reset() / cancel() and listen to session.bus. They never touch
the message list or the LLM themselves.
"""
import threading
import time
import uuid

from sage.chat import run_turn
from sage.events import EventBus


MAX_RECORD_SECONDS = 60.0     # a talk button that is never released (a crashed client) must not leave the mic open


class Session:
    def __init__(self, args, voice, toolbox, system: str, bus: EventBus | None = None, listener=None,
                 remote_listener=None):
        self.args = args
        self.voice = voice
        self.toolbox = toolbox
        self.system = system
        self.bus = bus or EventBus()
        self.messages = [{"role": "system", "content": system}]
        self._turn_lock = threading.Lock()
        self._pending: dict[str, dict] = {}        # confirmation id -> {"event", "approved", "reason"}
        self._pending_lock = threading.Lock()
        self.confirm_timeout = getattr(args, "confirm_timeout", 120.0)
        self.state_cb = None      # set by a client bridge (the server): called with "speaking", "listening", ...
        self.listener = listener  # speech input for a talk button (stt.listen.HoldToTalk), or None
        self.remote_listener = remote_listener   # the same, fed by a network client's microphone (PushRecorder)
        self._active = None       # the listener of the open recording
        self._source = None       # "server" or "client" while recording
        self._hf = False          # the wake-word listener is recording a request
        self._rec_lock = threading.Lock()
        self._recording = False
        self._rec_timer = None
        self.wake_listener = None
        if hasattr(voice, "on_speech_start"):
            voice.on_speech_start = lambda: self.set_state("speaking")

    @property
    def busy(self) -> bool:
        return self._turn_lock.locked()

    def send(self, text: str, source: str = "text") -> bool:
        """Runs one turn (blocks until the reply is finished and spoken). Returns False if a turn is already
        running: two replies must never talk over each other."""
        if not self._turn_lock.acquire(blocking=False):
            self.bus.emit("error", message="busy: a reply is still in progress", code="busy")
            return False
        try:
            self.bus.emit("user", text=text, source=source)
            run_turn(self.args, self.voice, self.messages, self.toolbox, text, self.bus.emit, self._confirm)
            self._trim()
            return True
        finally:
            self._turn_lock.release()

    # ---- speech input from a client's talk button (the microphone is on this machine)
    @property
    def can_talk(self) -> bool:
        return self.listener is not None

    @property
    def mic_busy(self) -> bool:
        """A microphone is open for a request: a talk button, a client's stream or the wake word."""
        return self._recording or self._hf

    @property
    def can_talk_client(self) -> bool:
        return self.remote_listener is not None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def recording_source(self) -> "str | None":
        return self._source if self._recording else None

    def feed_audio(self, data: bytes) -> None:
        """Microphone audio from a network client (16 kHz s16le mono). Ignored unless a client recording is open."""
        if self._recording and self._source == "client":
            self._active.feed(data)

    def talk_start(self, source: str = "server") -> bool:
        """Opens the microphone (source "server": this machine's; "client": a client streams its own). False if it
        cannot (no speech input, already recording, a reply in progress, or the device failed: then the user is told
        with a notice)."""
        listener = self.remote_listener if source == "client" else self.listener
        with self._rec_lock:
            if listener is None or self._recording or self._hf or self.busy:
                return False
            try:
                listener.start()
            except Exception as exc:   # noqa: BLE001 - a missing or busy microphone is a message, not a crash
                self.bus.emit("notice", level="warn", message=f"could not open the microphone: {exc}")
                return False
            self._recording, self._active, self._source = True, listener, source
            self._rec_timer = threading.Timer(MAX_RECORD_SECONDS, self._record_timeout)
            self._rec_timer.daemon = True
            self._rec_timer.start()
        self.set_state("listening")
        return True

    def talk_stop(self) -> "str | None":
        """Closes the microphone and transcribes. Returns the text, or None (with a notice saying why) if nothing
        usable was heard. Nothing happens if no recording is open."""
        with self._rec_lock:
            if not self._recording:
                return None
            self._recording = False
            if self._rec_timer:
                self._rec_timer.cancel()
        self.set_state("transcribing")
        try:
            text, note = self._active.finish()
        except Exception as exc:   # noqa: BLE001
            text, note = None, f"speech recognition failed: {exc}"
        if note:
            self.bus.emit("notice", level="warn", message=note)
        return text

    # ---- hands-free: the wake-word listener records by itself and hands over the finished audio
    def hands_free_begin(self) -> bool:
        with self._rec_lock:
            if self.listener is None or self._recording or self._hf or self.busy:
                return False
            self._hf = True
        self.set_state("listening")
        return True

    def hands_free_abort(self, note: "str | None" = None) -> None:
        self._hf = False
        if note:
            self.bus.emit("notice", level="info", message=note)
        self.set_state("idle")

    def hands_free_finish(self) -> bool:
        """Finishes an active hands-free recording immediately and transcribes what was said so far."""
        if self._hf and self.wake_listener is not None:
            self.wake_listener.finish_now()
            return True
        return False

    def hands_free_turn(self, audio) -> None:
        """Transcribes what the wake-word listener recorded and answers it like a spoken turn (blocks until the reply
        has been spoken)."""
        self.set_state("transcribing")
        try:
            text, note = self.listener.transcribe_audio(audio)
        except Exception as exc:   # noqa: BLE001
            text, note = None, f"speech recognition failed: {exc}"
        finally:
            self._hf = False
        if note:
            self.bus.emit("notice", level="warn", message=note)
        if text:
            self.send(text, "voice")
        self.set_state("idle")

    def abort_talk(self, note: "str | None" = None) -> None:
        """Closes the microphone and drops the audio (Stop, the client went away, the recording ran too long)."""
        with self._rec_lock:
            if not self._recording:
                return
            self._recording = False
            if self._rec_timer:
                self._rec_timer.cancel()
            try:
                self._active.abort()
            except Exception:   # noqa: BLE001
                pass
        if note:
            self.bus.emit("notice", level="warn", message=note)
        self.set_state("idle")

    def _record_timeout(self) -> None:
        self.abort_talk(f"recording stopped after {MAX_RECORD_SECONDS:.0f} s without release")

    def set_state(self, value: str) -> None:
        """Tells the client bridge what the assistant is doing (idle, listening, transcribing, thinking, speaking,
        awaiting_confirm). A no-op when nobody listens, so the terminal client is unaffected."""
        cb = self.state_cb
        if cb is not None:
            cb(value)

    def history(self) -> list[dict]:
        """The visible conversation as [{role, text, source}], for a client that just connected. Tool plumbing and
        the system prompt are left out; `source` is not tracked per message, so it is None."""
        out = []
        for m in self.messages[1:]:
            if m["role"] in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
                out.append({"role": m["role"], "text": m["content"], "source": None})
        return out

    def pending_confirms(self) -> list[dict]:
        """Open confirmation questions: [{id, tool, summary, arguments, timeout_left}]."""
        now = time.monotonic()
        with self._pending_lock:
            return [{**slot["info"], "timeout_left": max(0.0, slot["deadline"] - now)}
                    for slot in self._pending.values() if not slot["event"].is_set()]

    def cancel(self) -> None:
        """Stops speech now and says no to any open question. (Ctrl+C in the terminal does the same inside
        run_turn.)"""
        with self._pending_lock:
            for slot in self._pending.values():
                if not slot["event"].is_set():
                    slot["reason"] = "cancelled"
                    slot["event"].set()
        self.abort_talk()
        if self._hf:
            self.hands_free_abort()
        self.voice.cancel()

    # ---- tool confirmation: a tool asks, any client answers
    def answer(self, confirm_id: str, approved: bool) -> bool:
        """Answers a "confirm" event. Returns False if that question is unknown or already settled
        (first answer wins)."""
        with self._pending_lock:
            slot = self._pending.get(confirm_id)
            if slot is None or slot["event"].is_set():
                return False
            if time.monotonic() > slot["deadline"]:     # answered after --confirm-timeout (e.g. a blocked prompt)
                slot["approved"], slot["reason"] = False, "timeout"
                slot["event"].set()
                return False
            slot["approved"], slot["reason"] = bool(approved), "answered"
            slot["event"].set()
            return True

    def _confirm(self, tool, kwargs: dict, summary: str) -> bool:
        """Called from the turn thread by run_tool. Emits a "confirm" event and waits for answer(). A client
        may answer from inside its event handler (the terminal does); a timeout counts as no."""
        cid = uuid.uuid4().hex[:8]
        slot = {"event": threading.Event(), "approved": False, "reason": "timeout",
                "deadline": time.monotonic() + self.confirm_timeout,
                "info": {"id": cid, "tool": tool.name, "summary": summary, "arguments": kwargs,
                         "timeout": self.confirm_timeout}}
        with self._pending_lock:
            self._pending[cid] = slot
        try:
            self.bus.emit("confirm", **slot["info"])
            slot["event"].wait(max(0.0, slot["deadline"] - time.monotonic()))
            with self._pending_lock:
                slot["event"].set()                # settle it: a late answer() must not count
                approved = slot["approved"] and slot["reason"] == "answered"
            self.bus.emit("confirm_done", id=cid, approved=approved, reason=slot["reason"])
            return approved
        finally:
            with self._pending_lock:
                self._pending.pop(cid, None)

    def reset(self) -> None:
        self.messages = self.messages[:1]
        if self.toolbox is not None:
            self.toolbox.reset_taint()
        self.bus.emit("reset")

    def _trim(self) -> None:
        """Keeps the history from growing without limit, in big steps rather than one pair per turn.

        llama-server reuses the part of the prompt that is unchanged since the last request (its prompt cache).
        Dropping the oldest pair every turn once the history is full changes the start of the prompt every time,
        so the whole history is read again on every turn and the first token comes later. Instead nothing is
        dropped until the history passes max_history, and then it is cut back to trim_to (half by default): the
        cache is lost only once every few turns."""
        high = self.args.max_history
        if len(self.messages) <= 1 + high:
            return
        low = getattr(self.args, "trim_to", None) or max(2, high // 2)
        low = min(low, high)
        self.messages = self.messages[:1] + self.messages[-low:]
        while len(self.messages) > 1 and self.messages[1]["role"] != "user":
            self.messages.pop(1)   # history must start with a user turn (Gemma's template)
