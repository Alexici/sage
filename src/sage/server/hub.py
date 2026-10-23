"""The hub: connects the engine (plain threads, a synchronous event bus) to any number of WebSocket clients
(asyncio). Every message a client sees goes through here, numbered in one sequence.

Threads: the engine emits from its own threads; each emit is handed to the event loop with call_soon_threadsafe,
which keeps order. Everything below the 'loop thread' line runs on the loop and needs no locks.
"""
import asyncio
import json
import sys
import threading
import time

from sage.server.protocol import STATES, audio_info, build_hello

QUEUE_SIZE = 2000      # messages a slow client may fall behind before it is dropped (about 30 s of tokens)


class Client:
    def __init__(self, device: str, local: bool, token_hash: str):
        self.device, self.local, self.token_hash = device, local, token_hash
        self.q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.dropped = False
        self.audio = False          # wants the speech as binary frames
        self.audio_open = False     # an audio_start was sent and no audio_stop since


class Hub:
    def __init__(self, session):
        self.session = session
        self.loop = None
        self.clients: set = set()
        self.seq = 0
        self.state = "idle"
        self.turn_active = False
        self.mic_owner = None       # the client whose microphone frames are accepted
        self.audio_count = 0        # clients that want playback (read from the voice thread: a plain int)
        self._unsub = None

    # ------------------------------------------------------------------ wiring
    def attach(self, loop) -> None:
        self.loop = loop
        self._unsub = self.session.bus.subscribe(self._from_bus)
        self.session.state_cb = self.set_state
        sink = getattr(self.session.voice, "sink", None)
        if hasattr(sink, "flush"):
            sink.hub = self

    def detach(self) -> None:
        if self._unsub:
            self._unsub()
        self.session.state_cb = None
        sink = getattr(self.session.voice, "sink", None)
        if getattr(sink, "hub", None) is self:
            sink.hub = None

    # ------------------------------------------------------------------ any thread
    def _from_bus(self, event: dict) -> None:
        loop = self.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._on_event, event)

    def set_state(self, value: str) -> None:
        loop = self.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._set_state, value)

    # ------------------------------------------------------------------ loop thread
    def _on_event(self, event: dict) -> None:
        kind = event["type"]
        if kind == "confirm":
            self._set_state("awaiting_confirm")          # before the question itself, so the UI is ready
        self._broadcast({k: v for k, v in event.items() if k != "seq"})
        if kind in ("user", "confirm_done"):
            self._set_state("thinking")

    def _set_state(self, value: str) -> None:
        if value not in STATES or value == self.state:
            return
        self.state = value
        self._broadcast({"type": "state", "value": value})

    def _stamp(self, msg: dict) -> str:
        self.seq += 1
        msg["seq"] = self.seq
        msg.setdefault("ts", time.time())
        return json.dumps(msg, default=str)

    def _broadcast(self, msg: dict) -> None:
        payload = self._stamp(msg)
        for client in list(self.clients):
            self._put(client, payload)

    def _put(self, client: Client, payload) -> None:
        try:
            client.q.put_nowait(payload)
        except asyncio.QueueFull:
            self.drop(client)

    def drop(self, client: Client) -> None:
        """A client that cannot keep up is cut off (it reconnects and gets a fresh hello) rather than slowing
        everyone down or growing memory without limit."""
        if not client.dropped:
            client.dropped = True
            self.clients.discard(client)
            self._recount()
            print(f"[server] dropped slow client {client.device!r}", file=sys.stderr)

    def direct(self, client: Client, msg: dict) -> None:
        self._put(client, self._stamp(msg))

    def error(self, client: Client, code: str, message: str) -> None:
        self.direct(client, {"type": "error", "message": message, "code": code})

    def add(self, client: Client) -> None:
        """Registers a client. Its hello is queued first, in the same loop step, so nothing can come before it."""
        hello = build_hello(self.session, client.device, client.local, self.turn_active, self.state)
        self.direct(client, hello)
        wl = getattr(self.session, "wake_listener", None)
        if wl is not None:
            self.direct(client, {"type": "wake", "enabled": wl.enabled, "phrase": getattr(wl, "label", "Hey Sage")})
        self.clients.add(client)

    def remove(self, client: Client) -> None:
        self.clients.discard(client)
        self._recount()
        if client is self.mic_owner:
            self.mic_owner = None
            self.session.abort_talk()              # its microphone went away with it
        if not self.clients:
            # Nobody left to answer: an open question is a no, now rather than after the timeout, and a talk button
            # that was never released must not leave the microphone open.
            for q in self.session.pending_confirms():
                self.session.answer(q["id"], False)
            self.session.abort_talk()

    # ------------------------------------------------------------------ audio to clients
    def _recount(self) -> None:
        self.audio_count = sum(1 for c in self.clients if c.audio)

    def wants_audio(self) -> bool:
        """Any thread: is a client waiting for the speech? (Then the PC speakers stay quiet.)"""
        return self.audio_count > 0

    def set_audio(self, client: Client, on: bool) -> None:
        """Loop thread: a client turned playback on or off. Off while a stream is open stops it cleanly."""
        client.audio = on
        if not on and client.audio_open:
            client.audio_open = False
            self.direct(client, {"type": "audio_stop"})
        self._recount()

    def send_audio(self, pcm: bytes, valid=None) -> None:
        """Any thread (the voice player): one frame of speech for every client that wants it. `valid` is asked again on
        the loop thread: a frame handed over just before a Stop must not reach the client after its audio_stop."""
        loop = self.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._audio_frame, pcm, valid)

    def flush_audio(self) -> None:
        """Any thread: the reply was cancelled; every open stream is told to drop its buffer and stop."""
        loop = self.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._audio_flush)

    def _audio_frame(self, pcm: bytes, valid=None) -> None:
        if valid is not None and not valid():
            return
        info = audio_info(self.session)
        for client in list(self.clients):
            if not client.audio:
                continue
            if not client.audio_open:
                client.audio_open = True
                self.direct(client, {"type": "audio_start", **{k: info[k] for k in ("format", "rate", "channels")}})
            self._put(client, pcm)

    def _audio_flush(self) -> None:
        for client in list(self.clients):
            if client.audio_open:
                client.audio_open = False
                self.direct(client, {"type": "audio_stop"})

    # ------------------------------------------------------------------ turns
    def start_turn(self, text: str, source: str = "text") -> bool:
        """Runs one turn on its own thread. False if a turn is already running."""
        if self.turn_active or self.session.busy or self.session.mic_busy:
            return False
        self.turn_active = True
        threading.Thread(target=self._turn, args=(self._send_turn, text, source), daemon=True).start()
        return True

    def start_talk_stop(self) -> bool:
        """Ends the recording: transcribe on its own thread, then answer like a typed message. A stop without an
        open recording (the start was refused, the user let go anyway) is ignored, not an error."""
        if getattr(self.session, "_hf", False):
            self.session.hands_free_finish()
            return True
        if not self.session.recording:
            return True
        if self.turn_active or self.session.busy:
            return False
        self.turn_active = True
        threading.Thread(target=self._turn, args=(self._talk_turn,), daemon=True).start()
        return True

    def _send_turn(self, text, source):
        self.session.send(text, source)

    def _talk_turn(self):
        text = self.session.talk_stop()
        if text:
            self.session.send(text, "voice")

    def _turn(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:   # noqa: BLE001 - a bug must reach the client, not vanish in a thread
            print(f"[server] turn failed: {exc!r}", file=sys.stderr)
            self.session.bus.emit("error", message=f"internal error: {exc}", code="internal")
        finally:
            loop = self.loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._turn_done)

    def _turn_done(self) -> None:
        self.turn_active = False
        self._set_state("idle")