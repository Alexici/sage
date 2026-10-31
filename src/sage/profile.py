"""The saved profile: everything the user chose in the setup page (`data/profile.json`).

Written only by the server and the launcher. Fields hold ids and short text, never paths or shell strings. A change
is validated as a whole against a Catalog (what exists on this machine) before anything is saved or applied, so a
bad value can never reach the engine."""
import json
import os
import tempfile
import threading
import unicodedata
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from sage import models as llm_models

VERSION = 1
LIMITS = {"user_name": 40, "location": 80, "about": 500}
STT_MODELS = ("tiny.en", "base.en", "small.en", "medium.en")
LANGUAGES = ("en",)                         # English only: the voices cannot speak anything else well
CLOSE_ACTIONS = ("background", "quit_all")
ROLES = ("host", "client")
DEFAULT_TOOL_NAMES = ("get_time", "web_search", "read_webpage", "get_clipboard", "set_clipboard",
                      "list_files", "read_file", "write_file", "edit_file", "delete_file")


class ProfileError(Exception):
    """A change that cannot be applied. `code` goes on the wire (invalid), `field` names the setting."""

    def __init__(self, message: str, field: str | None = None, code: str = "invalid"):
        super().__init__(message)
        self.message, self.field, self.code = message, field, code


@dataclass(frozen=True)
class Profile:
    user_name: str = ""
    location: str = ""
    about: str = ""
    llm_model: str = llm_models.DEFAULT_MODEL
    ctx_size: int = llm_models.DEFAULT_CTX
    prompt_id: str = "sarcastic_companion"
    tts_backend: str = "chatterbox"
    voice_id: str = "shadowheart"
    stt_model: str = "small.en"
    stt_lang: str = "en"
    wake_enabled: bool = False
    wake_model_id: str = ""
    enabled_tools: tuple[str, ...] = DEFAULT_TOOL_NAMES
    close_action: str = "quit_all"
    role: str = "host"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["enabled_tools"] = list(self.enabled_tools)
        return d


FIELD_TYPES = {f.name: f.type for f in fields(Profile)}


@dataclass(frozen=True)
class Catalog:
    """What exists on this machine right now. Built by the server from the real registries; tests build their own."""
    models: frozenset = frozenset()
    prompts: frozenset = frozenset()
    tts_backends: frozenset = frozenset()
    voices: dict = field(default_factory=dict)       # backend id -> set of voice ids
    wake_models: frozenset = frozenset()
    tools: tuple = ()                                # tool names, in display order
    stt_models: tuple = STT_MODELS
    languages: tuple = LANGUAGES
    contexts: tuple = llm_models.CONTEXTS
    close_actions: tuple = CLOSE_ACTIONS


# --------------------------------------------------------------------------- text cleaning
def clean_line(value, name: str, limit: int, allow_empty: bool = True) -> str:
    """One trimmed line of text. Newlines and tabs become spaces; other control and invisible formatting
    characters (zero-width, right-to-left overrides) are refused, so the text cannot hide anything in a prompt."""
    if not isinstance(value, str):
        raise ProfileError("This must be text.", name)
    out = []
    for ch in value:
        if ch in "\r\n\t":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            raise ProfileError("This contains characters that cannot be used.", name)
        else:
            out.append(ch)
    text = " ".join("".join(out).split())
    if len(text) > limit:
        raise ProfileError(f"This is too long (at most {limit} characters).", name)
    if not text and not allow_empty:
        raise ProfileError("This cannot be empty.", name)
    return text


# --------------------------------------------------------------------------- validation
def _choice(value, name: str, allowed) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ProfileError("That choice is not available.", name)
    return value


def _validate_field(name: str, value, cat: Catalog):
    if name in LIMITS:
        return clean_line(value, name, LIMITS[name])
    if name == "llm_model":
        return _choice(value, name, cat.models)
    if name == "ctx_size":
        if isinstance(value, bool) or not isinstance(value, int) or value not in cat.contexts:
            raise ProfileError("That context size is not available.", name)
        return value
    if name == "prompt_id":
        return _choice(value, name, cat.prompts)
    if name == "tts_backend":
        return _choice(value, name, cat.tts_backends)
    if name == "voice_id":
        if not isinstance(value, str) or not value:
            raise ProfileError("Choose a voice.", name)
        return value                                   # checked against the backend afterwards
    if name == "stt_model":
        return _choice(value, name, cat.stt_models)
    if name == "stt_lang":
        return _choice(value, name, cat.languages)
    if name == "wake_enabled":
        if not isinstance(value, bool):
            raise ProfileError("This must be on or off.", name)
        return value
    if name == "wake_model_id":
        if value == "":
            return ""
        return _choice(value, name, cat.wake_models)
    if name == "enabled_tools":
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise ProfileError("This must be a list of tool names.", name)
        unknown = [v for v in value if v not in cat.tools]
        if unknown:
            raise ProfileError(f"Unknown tool: {unknown[0][:40]}.", name)
        return tuple(dict.fromkeys(value))             # no duplicates, order kept
    if name == "close_action":
        return _choice(value, name, cat.close_actions)
    if name == "role":
        return _choice(value, name, ROLES)
    raise ProfileError("Unknown setting.", name)


def apply(current: Profile, patch, cat: Catalog) -> tuple[Profile, tuple[str, ...]]:
    """The new profile and the names of the fields that really changed. Raises ProfileError; nothing is partial."""
    if not isinstance(patch, dict):
        raise ProfileError("The change must be an object.")
    clean = {}
    for key, value in patch.items():
        if key not in FIELD_TYPES:
            raise ProfileError("Unknown setting.", str(key)[:40])
        clean[key] = _validate_field(key, value, cat)
    new = replace(current, **clean)
    if new.voice_id not in cat.voices.get(new.tts_backend, ()):
        raise ProfileError("That voice does not belong to the chosen speech engine.", "voice_id")
    if new.wake_enabled and not new.wake_model_id:
        raise ProfileError("Choose a wake word model first.", "wake_model_id")
    changed = tuple(k for k in clean if getattr(new, k) != getattr(current, k))
    return new, changed


@dataclass(frozen=True)
class Impact:
    stages: tuple[str, ...]      # what must reload: llm, tts, stt (in this order); anything else applies at once
    suggest_reset: bool          # the persona changed: the old tone in the history will stick


def impact(changed) -> Impact:
    ch = set(changed)
    stages = tuple(s for s, names in (("llm", {"llm_model", "ctx_size"}), ("tts", {"tts_backend", "voice_id"}),
                                      ("stt", {"stt_model", "stt_lang"})) if ch & names)
    return Impact(stages, "prompt_id" in ch)


def raised_power(old: Profile, new: Profile, power_tools) -> tuple[str, ...]:
    """Power tools that this change newly switches on."""
    return tuple(t for t in new.enabled_tools if t in power_tools and t not in old.enabled_tools)


# --------------------------------------------------------------------------- storage
def _lenient(data: dict) -> Profile:
    """Builds a Profile from saved data without catalog checks (the catalog can change between runs; the server
    reports a model that vanished as degraded). Wrong types and oversized text fall back to the default."""
    base, out = Profile(), {}
    for f in fields(Profile):
        if f.name not in data:
            continue
        value = data[f.name]
        try:
            if f.name in LIMITS:
                out[f.name] = clean_line(value, f.name, LIMITS[f.name])
            elif f.name == "enabled_tools":
                if isinstance(value, list) and all(isinstance(v, str) for v in value):
                    out[f.name] = tuple(dict.fromkeys(value))
            elif f.name == "ctx_size":
                if isinstance(value, int) and not isinstance(value, bool) and 512 <= value <= 1_000_000:
                    out[f.name] = value
            elif f.name == "wake_enabled":
                if isinstance(value, bool):
                    out[f.name] = value
            elif isinstance(value, str) and len(value) <= 80 and f.name not in LIMITS:
                if f.name == "close_action" and value not in CLOSE_ACTIONS:
                    continue
                if f.name == "role" and value not in ROLES:
                    continue
                out[f.name] = value
        except ProfileError:
            continue
    return replace(base, **out)


def _migrate(data):
    """Brings saved data to the current version. A bare object (hand-written, version 0) is accepted."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    if "version" not in data:
        data = {"version": 1, "profile": data}
    if data["version"] > VERSION:
        raise ValueError(f"saved by a newer Sage (version {data['version']})")
    if not isinstance(data.get("profile"), dict):
        raise ValueError("no profile inside")
    return data["profile"]


class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> Profile | None:
        """None when there is no profile yet (first run) or the file is unusable (it is moved to .bad)."""
        with self._lock:
            try:
                text = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except OSError:
                return None
            try:
                return _lenient(_migrate(json.loads(text)))
            except (ValueError, RecursionError):
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
                except OSError:
                    pass
                return None

    def save(self, profile: Profile) -> None:
        """Atomic: a crash or a full disk leaves the previous file intact. Readable by the owner only."""
        body = json.dumps({"version": VERSION, "profile": profile.to_dict()}, indent=2, ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
