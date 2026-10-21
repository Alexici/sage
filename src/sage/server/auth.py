"""Who may connect: paired devices (a named, revocable token each), no browsers, and an optional address allowlist.

Tokens are random and long, so a plain SHA-256 of the token is enough to store (nothing to brute-force). Only
the hash is saved; the token itself is shown once when the device is added."""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceStore:
    """Paired devices, kept in a small JSON file (or in memory when path is None, as in --demo)."""

    def __init__(self, path: "Path | None"):
        self.path = Path(path) if path else None
        self._devices: dict = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._devices = data.get("devices", {}) if isinstance(data, dict) else {}
        except FileNotFoundError:
            self._devices = {}
        except (ValueError, OSError):
            self._devices = {}      # unreadable file: nobody is paired (fail closed), `devices add` rewrites it

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".devices-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"devices": self._devices}, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add(self, name: str) -> str:
        """Pairs a device and returns its token (the only time it exists in clear text)."""
        if not NAME_RE.fullmatch(name):
            raise ValueError("a device name is 1-32 letters, digits, dots, dashes or underscores")
        with self._lock:
            self._load()
            if name in self._devices:
                raise ValueError(f"a device called {name!r} already exists; remove it first")
            token = "sage_" + secrets.token_urlsafe(32)
            self._devices[name] = {"hash": hash_token(token), "created": int(time.time())}
            self._save()
        return token

    def remove(self, name: str) -> bool:
        with self._lock:
            self._load()
            if name not in self._devices:
                return False
            del self._devices[name]
            self._save()
        return True

    def names(self) -> list:
        with self._lock:
            self._load()
            return sorted(self._devices)

    def created(self, name: str) -> "int | None":
        with self._lock:
            return (self._devices.get(name) or {}).get("created")

    def verify(self, token: str) -> "str | None":
        """The device name for this token, or None. Re-reads the file, so a removed device stops working."""
        if not token:
            return None
        digest = hash_token(token)
        with self._lock:
            self._load()
            found = None
            for name, rec in self._devices.items():     # no early exit: same work whatever the answer
                if hmac.compare_digest(str(rec.get("hash", "")), digest):
                    found = name
            return found

    def verify_hash(self, digest: str) -> "str | None":
        """Like verify, for a connection that already proved its token: is that device still paired?"""
        with self._lock:
            self._load()
            for name, rec in self._devices.items():
                if hmac.compare_digest(str(rec.get("hash", "")), digest):
                    return name
        return None


def bearer_token(header: "str | None") -> "str | None":
    """The token from an 'Authorization: Bearer <token>' header."""
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def is_loopback(host: "str | None") -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class Allowlist:
    """Optional network allowlist. Empty means everyone who has a token; with networks, only those (and this
    machine itself, always)."""

    def __init__(self, cidrs=()):
        self.networks = [ipaddress.ip_network(c, strict=False) for c in cidrs]

    def allows(self, host: "str | None") -> bool:
        if not self.networks or is_loopback(host):
            return True
        try:
            addr = ipaddress.ip_address(host or "")
        except ValueError:
            return False
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self.networks)
