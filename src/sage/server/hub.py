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

from sage.server.protocol import STATES, build_hello

QUEUE_SIZE = 2000      # messages a slow client may fall behind before it is dropped (about 30 s of tokens)


class Client:
    def __init__(self, device: str, local: bool, token_hash: str):
        self.device, self.local, self.token_hash = device, local, token_hash
        self.q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.dropped = False


class Hub:
    def __init__(self, session):
        self.session = session
        self.loop = None
        self.clients: set = set()
        self.seq = 0
        self.state = "idle"
        self.turn_active = False
        self._unsub = None

    # ------------------------------------------------------------------ wiring
    def attach(self, loop) -> None:
        self.loop = loop
        self._unsub = self.session.bus.subscribe(self._from_bus)
        self.session.state_cb = self.set_state

    def detach(self) -> None:
        if self._unsub:
            self._unsub()
        self.session.state_cb = None

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

    def _put(self, client: Client, payload: str) -> None:
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
            print(f"[server] dropped slow client {client.device!r}", file=sys.stderr)

    def direct(self, client: Client, msg: dict) -> None:
        self._put(client, self._stamp(msg))

    def error(self, client: Client, code: str, message: str) -> None:
        self.direct(client, {"type": "error", "message": message, "code": code})

    def add(self, client: Client) -> None:
        """Registers a client. Its hello is queued first, in the same loop step, so nothing can come before it."""
        hello = build_hello(self.session, client.device, client.local, self.turn_active, self.state)
        self.direct(client, hello)
        self.clients.add(client)

    def remove(self, client: Client) -> None:
        self.clients.discard(client)
        if not self.clients:
            # Nobody left to answer: an open question is a no, now rather than after the timeout, and a talk button
            # that was never released must not leave the microphone open.
            for q in self.session.pending_confirms():
                self.session.answer(q["id"], False)
            self.session.abort_talk()

    # ------------------------------------------------------------------ turns
    def start_turn(self, text: str, source: str = "text") -> bool:
        """Runs one turn on its own thread. False if a turn is already running."""
        if self.turn_active or self.session.busy or self.session.recording:
            return False
        self.turn_active = True
        threading.Thread(target=self._turn, args=(self._send_turn, text, source), daemon=True).start()
        return True

    def start_talk_stop(self) -> bool:
        """Ends the recording: transcribe on its own thread, then answer like a typed message. A stop without an
        open recording (the start was refused, the user let go anyway) is ignored, not an error."""
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
