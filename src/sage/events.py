"""Events: the only way the engine talks to a screen.

The engine (chat, session, tools) never prints. It emits small JSON-serializable dicts onto an EventBus,
and every client (the terminal today, a web UI later) subscribes and draws them its own way.

Every event has "type", "seq" (1, 2, 3, ... per bus) and "ts" (unix time) plus its own fields:

  user          text, source ("text" | "voice")          a user message was accepted
  token         text                                      a piece of the reply, as it streams
  reply_end                                               the reply text is complete (speech may still play)
  tool_start    name, arguments (JSON string)             a tool call is about to run
  tool          name, arguments, chars, seconds, tier, outcome   a tool call finished (outcome: ran, error,
                                                          denied, blocked, unknown)
  confirm       id, tool, summary, arguments, timeout     a tool wants the user's yes/no: answer with Session.answer(id, ok)
  confirm_done  id, approved, reason                      the question is settled (reason: answered, timeout, cancelled)
  stopped                                                 the user interrupted the reply
  error         message, code (busy | llm, optional)      the turn failed (LLM down, busy, ...)
  latency       first_token, first_chunk_ready, real_start, first_chunk, held, gaps, filler, tools
  reset                                                   conversation cleared

  notice        level (info | warn), message              a non-fatal remark (microphone problems, "too short", ...)

The network server (sage.server) adds `hello` and `state` on top of these; it numbers its own messages.
Later additions are new types: clients ignore types they do not know.
"""
import sys
import threading
import time
from typing import Callable

Event = dict


class EventBus:
    def __init__(self):
        self._subs: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()
        self._seq = 0

    def subscribe(self, fn: Callable[[Event], None]) -> Callable[[], None]:
        """Registers a listener. Returns a function that removes it again."""
        with self._lock:
            self._subs.append(fn)

        def unsubscribe():
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)
        return unsubscribe

    def emit(self, type: str, **data) -> Event:
        """Builds an event and hands it to every listener, in the calling thread, in subscription order.
        A listener that raises is reported on stderr and skipped: one broken client must not kill a turn."""
        with self._lock:
            self._seq += 1
            event = {"type": type, "seq": self._seq, "ts": time.time(), **data}
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception as exc:   # noqa: BLE001
                print(f"[event listener failed on {type!r}: {exc!r}]", file=sys.stderr)
        return event
