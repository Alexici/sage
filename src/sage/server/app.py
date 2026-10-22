"""The FastAPI app: /health and the /ws conversation socket.

A connection must pass, in this order: no browser Origin header, an allowed network address, a valid device
token. Only then is the socket accepted and the hello sent. Denied attempts are logged without any token."""
import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect

from sage.server.auth import Allowlist, DeviceStore, bearer_token, hash_token, is_loopback
from sage.server.hub import Client, Hub
from sage.server.protocol import MAX_FRAME, PROTOCOL, ProtocolError, audio_info, parse_client_message

REVOKE_CHECK_SECONDS = 15.0


def create_app(session, devices: DeviceStore, allow: "Allowlist | None" = None,
               revoke_check: float = REVOKE_CHECK_SECONDS) -> FastAPI:
    allow = allow or Allowlist()
    hub = Hub(session)

    @asynccontextmanager
    async def lifespan(_app):
        hub.attach(asyncio.get_running_loop())
        try:
            yield
        finally:
            hub.detach()

    app = FastAPI(title="Sage", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub

    @app.get("/health")
    async def health():
        return {"ok": True, "protocol": PROTOCOL}

    async def deny(ws: WebSocket, status: int, why: str, host: str) -> None:
        print(f"[server] refused {host or '?'}: {why}", file=sys.stderr)
        try:
            await ws.send_denial_response(PlainTextResponse(why, status_code=status))
        except Exception:   # noqa: BLE001 - a server without the denial extension: close before accept (403)
            await ws.close(code=1008)

    @app.websocket("/ws")
    async def conversation(ws: WebSocket):
        host = ws.client.host if ws.client else ""
        if "origin" in ws.headers:      # a web page, not our app: browsers do not stop pages reaching localhost
            return await deny(ws, 403, "browser origins are not allowed", host)
        if not allow.allows(host):
            return await deny(ws, 403, "address not allowed", host)
        token = bearer_token(ws.headers.get("authorization"))
        device = devices.verify(token) if token else None
        if device is None:
            return await deny(ws, 401, "missing or unknown device token", host)

        await ws.accept()
        client = Client(device, is_loopback(host), hash_token(token))
        hub.add(client)
        tasks = [asyncio.create_task(_sender(ws, client)),
                 asyncio.create_task(_revocation_watch(devices, hub, client, revoke_check))]
        try:
            await _receive(ws, client, hub, session)
        finally:
            for t in tasks:
                t.cancel()
            hub.remove(client)

    return app


async def _sender(ws: WebSocket, client: Client) -> None:
    """Writes queued messages to one client. A client that was dropped (too slow) or revoked is closed here."""
    try:
        while True:
            if client.dropped:
                await ws.close(code=1013)       # try again later
                return
            try:
                payload = await asyncio.wait_for(client.q.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            if isinstance(payload, bytes):
                await ws.send_bytes(payload)
            else:
                await ws.send_text(payload)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass


async def _revocation_watch(devices: DeviceStore, hub: Hub, client: Client, every: float) -> None:
    """A device removed with `devices remove` is cut off within a few seconds, not at its next reconnect."""
    while not client.dropped:
        await asyncio.sleep(every)
        if devices.verify_hash(client.token_hash) is None:
            print(f"[server] device {client.device!r} was removed: disconnecting", file=sys.stderr)
            hub.drop(client)


async def _receive(ws: WebSocket, client: Client, hub: Hub, session) -> None:
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            return
        data = msg.get("bytes")
        if data is not None:                    # microphone audio from the client that opened the recording
            if len(data) > MAX_FRAME:
                hub.error(client, "bad_request", "audio frame too large")
            elif client is hub.mic_owner:
                session.feed_audio(data)
            continue
        raw = msg.get("text")
        if raw is None:
            hub.error(client, "bad_request", "unsupported frame")
            continue
        try:
            kind, data = parse_client_message(raw)
        except ProtocolError as exc:
            hub.error(client, exc.code, exc.message)
            continue
        await _dispatch(kind, data, client, hub, session)


async def _dispatch(kind: str, data: dict, client: Client, hub: Hub, session) -> None:
    if kind == "send":
        if not hub.start_turn(data["text"], "text"):
            hub.error(client, "busy", "a reply is still in progress")
    elif kind == "cancel":
        await asyncio.to_thread(session.cancel)
    elif kind == "reset":
        if hub.turn_active or session.busy:
            hub.error(client, "busy", "cannot clear the conversation during a reply")
        else:
            await asyncio.to_thread(session.reset)
    elif kind == "answer":
        session.answer(data["id"], data["approved"])     # unknown, late or second answers are ignored
    elif kind == "audio_config":
        if audio_info(session) is None:
            hub.error(client, "unsupported", "this server has no audio output")
        else:
            hub.set_audio(client, data["playback"])
    elif kind == "wake_config":
            wl = getattr(session, "wake_listener", None)
            if wl is None:
                hub.direct(client, {"type": "wake", "enabled": False})
                hub.direct(client, {"type": "notice", "level": "warn",
                                    "message": "Start the server with --wake hey_jarvis (or --wake path/to/hey_sage.onnx) to enable wake word."})
            else:
                wl.set_enabled(data["enabled"])
                hub._broadcast({"type": "wake", "enabled": wl.enabled, "phrase": getattr(wl, "label", "Hey Sage")})
    elif kind == "talk_start":
        source = data["source"]
        if source == "server" and not client.local:
            hub.error(client, "bad_request", "the server's microphone is on the server machine; send source "
                                             "'client' and stream your own microphone")
        elif (source == "server" and not session.can_talk) or (source == "client" and not session.can_talk_client):
            hub.error(client, "unsupported", "voice input is not available on this server (started without speech "
                                             "recognition, or faster-whisper is missing)")
        elif hub.turn_active or session.busy or session.mic_busy:
            hub.error(client, "busy", "a reply is still in progress" if not session.mic_busy
                      else "the microphone is already in use")
        else:
            ok = await asyncio.to_thread(session.talk_start, source)       # a failure arrives as a notice
            hub.mic_owner = client if ok and source == "client" else None
    elif kind == "talk_stop":
        if session.recording_source == "server" and not client.local:
            hub.error(client, "bad_request", "this recording is on the server's microphone")
        elif not hub.start_talk_stop():
            hub.error(client, "busy", "a reply is still in progress")
