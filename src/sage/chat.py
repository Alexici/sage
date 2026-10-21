"""One conversation turn: stream the reply, speak it chunk by chunk, run tools, report it all as events.

Nothing here prints: everything the user should see goes through emit(type, **data) (see events.py)."""
import threading
import time

import requests

from sage.llm import stream_llm
from sage.text import Chunker


def make_chunker(args) -> Chunker:
    return Chunker(first_cut_words=args.first_cut_words)


def _noop(*_a, **_k):
    return None


class _Stopped(Exception):
    """The reply was cancelled from another thread (Session.cancel, a Stop button)."""


def run_tool(args, voice, gen, toolbox, call, emit=_noop, confirm=None) -> str:
    """Runs one tool call through the permission tiers (Toolbox.run). If it is slow, a filler from the tool's
    category plays. `confirm(tool, kwargs, summary) -> bool` asks the user; without it, anything that needs
    confirmation is refused."""
    spec = toolbox.get(call["name"])
    stop = None
    if args.tool_filler_delay > 0:
        category = spec.filler if spec else "generic"
        stop = threading.Event()

        def tool_filler():
            if stop.wait(args.tool_filler_delay):
                return
            # An earlier filler (a "Hmm.") may still be playing or pausing: wait for it, then fill if the
            # tool is still running.
            while time.monotonic() < voice.filler_until:
                if stop.wait(0.05):
                    return
            voice.play_filler(gen, category)

        threading.Thread(target=tool_filler, daemon=True).start()

    def ask(tool, kwargs, summary):
        if stop:
            stop.set()      # nobody wants "give me a sec" while they are deciding
        return confirm(tool, kwargs, summary)

    emit("tool_start", name=call["name"], arguments=call["arguments"])
    t = time.monotonic()
    try:
        res = toolbox.run(call["name"], call["arguments"], ask if confirm else None)
    finally:
        if stop:
            stop.set()
    dt = time.monotonic() - t - res.confirm_wait   # the user's thinking time is not the tool's time
    voice.stats["tools"].append(f"{call['name']} {dt:.2f}s")
    emit("tool", name=call["name"], arguments=call["arguments"], chars=len(res.text), seconds=dt,
         tier=res.tier, outcome=res.outcome)
    return res.text


def run_turn(args, voice, messages, toolbox, user_text, emit=_noop, confirm=None):
    base_len = len(messages)
    messages.append({"role": "user", "content": user_text})
    gen = voice.new_reply()
    t0 = voice.t0

    def check():
        if voice.gen != gen:     # Voice.cancel() bumps the generation: this reply is dead
            raise _Stopped

    watch = None
    wait = {"delay": args.chat_filler_delay, "since": t0, "cat": "thinking", "paused": False}
    if args.chat_filler_delay > 0:
        # Waiting-filler: plays when nothing has been spoken yet and speech is still far away (see
        # Voice.try_wait_filler). Paused while a tool runs (the tool filler covers that), then re-armed.
        watch = voice.start_wait_watch(gen, wait)
    first_token, text, used_tools = None, "", False
    try:
        for _round in range(args.max_tool_rounds + 1):
            check()
            chunker = make_chunker(args)
            text, calls = "", []
            for kind, val in stream_llm(args, messages, toolbox.schemas() if toolbox else None):
                check()
                if kind == "content":
                    if first_token is None:
                        first_token = time.monotonic() - t0
                    text += val
                    emit("token", text=val)
                    for chunk in chunker.feed(val):
                        voice.say(gen, chunk)
                elif kind == "tool_start":
                    wait["paused"] = True       # a tool is coming: no "Hmm", the tool filler handles it
                else:
                    calls = val
            for chunk in chunker.flush():
                voice.say(gen, chunk)
            if not calls or _round == args.max_tool_rounds:
                break
            used_tools = True
            messages.append({
                "role": "assistant", "content": text,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls],
            })
            for c in calls:
                check()                 # a Stop must also stop the next tool call
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": run_tool(args, voice, gen, toolbox, c, emit, confirm)})
                spec = toolbox.get(c["name"])
                wait["cat"] = spec.filler if spec else "generic"
            # Tool(s) done, the model now has to write the answer: watch that wait, using the tool's filler set.
            wait.update(paused=False, since=time.monotonic(), delay=args.post_tool_filler_delay)
        voice.end(gen)
        emit("reply_end")
        voice.wait_done()
    except (KeyboardInterrupt, _Stopped):
        voice.cancel()
        emit("stopped")
        if used_tools:
            del messages[base_len:]
            return
    except (requests.RequestException, RuntimeError) as exc:
        voice.cancel()
        emit("error", message=str(exc), code="llm")
        del messages[base_len:]
        return
    finally:
        if watch:
            watch.set()
    if text:
        messages.append({"role": "assistant", "content": text})
    elif used_tools:
        messages.append({"role": "assistant", "content": "Done."})  # keep user/assistant turns alternating
    else:
        del messages[base_len:]  # empty reply: drop the turn, Gemma's template needs strict alternation
        return
    s = voice.stats
    emit("latency", first_token=first_token, first_chunk_ready=s.get("first_chunk_ready"),
         real_start=s.get("real_start"), first_chunk=s.get("first_chunk"), held=s.get("held", 0),
         gaps=s.get("gaps", 0), filler=s["filler"], tools=list(s["tools"]))
