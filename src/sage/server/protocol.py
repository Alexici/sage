"""Protocol version 1: what a client may send, and the hello message. The message tables are in the plan,
section 9.5. Everything a client sends is untrusted: it is parsed and checked here, nowhere else."""
import json

PROTOCOL = 1
MAX_TEXT = 4000
MAX_FRAME = 64 * 1024          # bytes; a message is one short JSON object
STATES = ("idle", "listening", "transcribing", "thinking", "speaking", "awaiting_confirm")


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def parse_client_message(raw: str) -> tuple:
    """Returns (type, fields) or raises ProtocolError(code='bad_request')."""
    if len(raw) > MAX_FRAME:
        raise ProtocolError("bad_request", "message too large")
    try:
        msg = json.loads(raw)
    except ValueError:
        raise ProtocolError("bad_request", "not valid JSON") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("bad_request", "a message is a JSON object with a string 'type'")
    kind = msg["type"]
    if kind == "send":
        text = msg.get("text")
        if not isinstance(text, str):
            raise ProtocolError("bad_request", "send needs a text string")
        text = text.strip()
        if not text:
            raise ProtocolError("bad_request", "empty message")
        if len(text) > MAX_TEXT:
            raise ProtocolError("bad_request", f"message too long (limit {MAX_TEXT} characters)")
        return kind, {"text": text}
    if kind in ("cancel", "reset", "talk_stop"):
        return kind, {}
    if kind == "talk_start":
        source = msg.get("source", "server")
        if source not in ("server", "client"):
            raise ProtocolError("bad_request", "talk_start source is 'server' or 'client'")
        return kind, {"source": source}
    if kind == "audio_config":
        playback = msg.get("playback")
        if not isinstance(playback, bool):
            raise ProtocolError("bad_request", "audio_config needs playback true or false")
        return kind, {"playback": playback}
    if kind == "wake_config":
        enabled = msg.get("enabled")
        if not isinstance(enabled, bool):
            raise ProtocolError("bad_request", "wake_config needs enabled true or false")
        return kind, {"enabled": enabled}
    if kind == "answer":
        cid, approved = msg.get("id"), msg.get("approved")
        if not isinstance(cid, str) or not 0 < len(cid) <= 64 or not isinstance(approved, bool):
            raise ProtocolError("bad_request", "answer needs an id string and approved true or false")
        return kind, {"id": cid, "approved": approved}
    raise ProtocolError("bad_request", f"unknown message type {kind[:40]!r}")


MIC_RATE = 16000


def audio_info(session) -> "dict | None":
    """What a client needs to play the speech and to send its microphone; None if this server has no audio path."""
    rate = getattr(getattr(session.voice, "sink", None), "sr", None)
    if not rate or not hasattr(session.voice.sink, "flush"):
        return None
    return {"format": "pcm_s16le", "rate": int(rate), "channels": 1, "mic_rate": MIC_RATE}


def build_hello(session, device: str, local: bool, busy: bool, state: str) -> dict:
    """The first message on every connection: everything a client needs to replace its whole picture."""
    pending = session.pending_confirms()
    tools = list(session.toolbox.tools) if session.toolbox else []
    return {
        "type": "hello", "protocol": PROTOCOL,
        "name": getattr(session.args, "name", "Sage"), "user": getattr(session.args, "user", ""),
        "device": device, "local": local, "can_talk": (local and session.can_talk) or session.can_talk_client,
        "audio": audio_info(session), "busy": busy, "state": state, "tools": tools,
        "history": session.history(), "pending_confirm": pending[0] if pending else None,
    }
