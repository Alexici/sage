"""Persona prompts: the presets, the user's own, and the one place a prompt becomes the system message.

Placeholders are replaced in ONE pass by exact token ({user}, {user_context}, {tools_summary}), never with Python
formatting, so braces or code in a prompt, and braces in the user's own notes, cannot break or expand anything."""
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from sage.profile import ProfileError, clean_line

PLACEHOLDERS = ("user", "user_context", "tools_summary")
MAX_PROMPT_CHARS = 8000
MAX_LABEL_CHARS = 40
# Presets shipped with Sage: stem of prompts/<stem>.txt -> (label, description, tested). Other files in prompts/
# (older versions kept by hand) are ignored on purpose.
BUILTIN = {
    "sarcastic_companion": ("Sarcastic Companion", "Dry, guarded, short answers.", True),
}
FILE_TOOLS = {"list_files", "read_file", "write_file", "edit_file", "delete_file"}
CHANGING = {"write_file", "edit_file", "delete_file", "set_clipboard", "run_command"}
TRASHING = {"write_file", "edit_file", "delete_file"}

_TOKEN = re.compile(r"(?m)(?P<line>^[ \t]*\{(?P<k>\w+)\}[ \t]*(?:\n|\Z))|\{(?P<k2>user|user_context|tools_summary)\}")


def render(template: str, user: str, user_context: str, tools_summary: str) -> str:
    """Fills the placeholders in one pass. A placeholder alone on its line disappears with the line when empty."""
    values = {"user": user or "the user", "user_context": user_context, "tools_summary": tools_summary}

    def sub(m: re.Match) -> str:
        if m.group("line") is not None:
            key = m.group("k")
            if key not in values:
                return m.group(0)
            v = values[key]
            return v + "\n" if v and m.group(0).endswith("\n") else v
        return values[m.group("k2")]

    return _TOKEN.sub(sub, template).strip()


def user_context(user_name: str, location: str, about: str) -> str:
    """What the user chose to tell Sage about themselves, as plain sentences (empty when they told nothing)."""
    parts = []
    if user_name:
        parts.append(f"Their name is {user_name}.")
    if location:
        parts.append(f"They said they are in {location}.")
    if about:
        parts.append(f"They also told you: {about}")
    return " ".join(parts)


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def tools_summary(tools, user: str = "") -> str:
    """What Sage can do, built from the tools that are really on, so the prompt never promises a missing one."""
    who = user or "the user"
    tools = list(tools)
    names = {t.name for t in tools}
    blurbs = [t.blurb for t in tools if t.blurb]
    if not blurbs:
        return ("You have no tools right now, so you cannot look anything up or touch any files; say so plainly "
                "when asked. Anything else you can't do yet, say so and offer the nearest thing.")
    out = [f"Your tools let you {_list(blurbs)}."]
    if names & FILE_TOOLS:
        out.append("File tools only see one workspace folder.")
    if names & CHANGING:
        out.append(f"Anything that changes or removes something asks {who} first"
                   + (", and files you replace or delete are kept in a trash folder." if names & TRASHING else "."))
    out.append("Commands you run stay in the workspace folder, and anything beyond harmless read-only ones asks first."
               if "run_command" in names else "You cannot run commands.")
    out.append("Anything else you can't do yet, say so and offer the nearest thing.")
    return " ".join(out)


@dataclass(frozen=True)
class PromptInfo:
    id: str
    label: str
    description: str
    builtin: bool
    tested: bool

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "description": self.description,
                "builtin": self.builtin, "tested": self.tested}


class PromptLibrary:
    """prompts/<preset>.txt for the presets, prompts/custom/<slug>.json for the user's own. Ids come from this
    listing only, so a made-up id can never turn into a path."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.custom_dir = self.root / "custom"
        self._lock = threading.Lock()

    def _custom_files(self) -> dict[str, Path]:
        if not self.custom_dir.is_dir():
            return {}
        return {f"custom-{p.stem}": p for p in sorted(self.custom_dir.glob("*.json")) if re.fullmatch(r"[a-z0-9-]+", p.stem)}

    def _read_custom(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and isinstance(data.get("label"), str) and isinstance(data.get("content"), str):
            return data
        return None

    def list(self) -> list[PromptInfo]:
        out = [PromptInfo(i, label, desc, True, tested) for i, (label, desc, tested) in BUILTIN.items()
               if (self.root / f"{i}.txt").is_file()]
        for pid, path in self._custom_files().items():
            data = self._read_custom(path)
            if data:
                out.append(PromptInfo(pid, data["label"][:MAX_LABEL_CHARS], "Your own persona.", False, False))
        return out

    def ids(self) -> frozenset[str]:
        return frozenset(p.id for p in self.list())

    def load(self, prompt_id: str) -> str:
        if prompt_id in BUILTIN and (self.root / f"{prompt_id}.txt").is_file():
            path = self.root / f"{prompt_id}.txt"
            try:
                return path.read_text(encoding="utf-8")[:MAX_PROMPT_CHARS * 4]
            except OSError as exc:
                raise ProfileError(f"The prompt could not be read ({exc.strerror}).", "prompt_id", "unavailable")
        path = self._custom_files().get(prompt_id) if isinstance(prompt_id, str) else None
        data = self._read_custom(path) if path else None
        if not data:
            raise ProfileError("That persona does not exist.", "prompt_id")
        return data["content"]

    def save(self, label, content, prompt_id: str | None = None) -> PromptInfo:
        label = clean_line(label, "label", MAX_LABEL_CHARS, allow_empty=False)
        if not isinstance(content, str) or not content.strip():
            raise ProfileError("The persona text cannot be empty.", "content")
        if len(content) > MAX_PROMPT_CHARS:
            raise ProfileError(f"The persona text is too long (at most {MAX_PROMPT_CHARS} characters).", "content")
        if any((ord(c) < 32 and c not in "\n\r\t") or c in "\x7f‪‫‬‭‮⁦⁧⁨⁩"
               for c in content):
            raise ProfileError("The persona text contains characters that cannot be used.", "content")
        with self._lock:
            existing = self._custom_files()
            if prompt_id is not None:
                if prompt_id not in existing:
                    raise ProfileError("You can only edit your own personas.", "id")
                slug = prompt_id[len("custom-"):]
            else:
                base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30].strip("-") or "persona"
                slug, n = base, 1
                while f"custom-{slug}" in existing:
                    n += 1
                    slug = f"{base}-{n}"
            self.custom_dir.mkdir(parents=True, exist_ok=True)
            target = self.custom_dir / f"{slug}.json"
            fd, tmp = tempfile.mkstemp(dir=self.custom_dir, prefix=".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"label": label, "content": content}, f, ensure_ascii=False)
                os.replace(tmp, target)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        return PromptInfo(f"custom-{slug}", label, "Your own persona.", False, False)

    def delete(self, prompt_id: str) -> None:
        with self._lock:
            path = self._custom_files().get(prompt_id) if isinstance(prompt_id, str) else None
            if path is None:
                raise ProfileError("You can only delete your own personas.", "id")
            path.unlink(missing_ok=True)
