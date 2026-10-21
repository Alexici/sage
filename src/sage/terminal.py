"""The terminal client: draws events as text. Output is meant to look exactly like the pre-event version."""


def short_args(arguments: str) -> str:
    return arguments if len(arguments) < 70 else arguments[:67] + "..."


def render(event: dict) -> None:
    t = event["type"]
    if t == "token":
        print(event["text"], end="", flush=True)
    elif t == "reply_end":
        print()
    elif t == "tool":
        outcome = event.get("outcome", "ran")
        print(f"\n  [tool] {event['name']}({short_args(event['arguments'])}) -> {event['chars']} chars "
              f"in {event['seconds']:.2f}s" + ("" if outcome == "ran" else f" [{outcome}]"))
    elif t == "stopped":
        print("\n[stopped]")
    elif t == "error":
        print(f"\n[LLM error: {event['message']}]" if not event["message"].startswith("busy") else
              f"[{event['message']}]")
    elif t == "confirm_done":
        if event["reason"] != "answered":
            print(f"  [confirmation {event['reason']}: nothing was done]")
    elif t == "reset":
        print("[conversation cleared]")
    elif t == "user":
        if event["source"] == "voice":
            print(f"you (voice)> {event['text']}")
    elif t == "latency":
        print(latency_line(event))


def latency_line(e: dict) -> str:
    def fmt(k):
        return f"{e[k]:.2f}s" if e.get(k) is not None else "n/a"
    return (f"  [latency] first token {fmt('first_token')} | first audio ready {fmt('first_chunk_ready')} | "
            f"speech started {fmt('real_start')} | first chunk {e.get('first_chunk') or 'n/a'} | "
            f"held {e.get('held', 0):.1f}s | gaps {e.get('gaps', 0):.1f}s | filler {e.get('filler') or 'no'}"
            + (f" | tools: {', '.join(e['tools'])}" if e.get("tools") else ""))


def confirm_listener(session, ask=input):
    """Terminal side of tool confirmation: shows what is about to happen and answers y/N. Anything but a clear
    yes (including Ctrl+C or end of input) is a no. Subscribe it after `render`."""
    def on_event(event: dict) -> None:
        if event["type"] != "confirm":
            return
        print(f"\n  [confirm] {event['summary']}")
        try:
            reply = ask("  allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        session.answer(event["id"], reply in ("y", "yes"))
    return on_event
