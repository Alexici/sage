"""Tools the assistant can call, and the permission tiers that guard them.

A tool is a Python function plus a JSON schema the model sees, and a `filler` category that decides which
"give me a sec" clips play if the tool is slow (see FILLER_SETS in sage/fillers.py).

PERMISSION TIERS (enforced in Toolbox.run, in code, not in the prompt):
  auto     runs straight away: reads, and writes that cannot destroy anything (creating a NEW file)
  confirm  the user is asked first (Session.confirm -> a "confirm" event -> y/N): editing, overwriting,
           deleting, and any command that is not on the harmless-read-only list
  never    refused outright, whatever the model says or the user's session allows

A tool has a fixed `tier`, or a `classify(kwargs)` function that decides per call (a file write is auto when
the file is new and confirm when it would replace something). `describe(kwargs)` writes the sentence the user
sees in the confirmation; it is built from the real arguments by code, never from the model's own words.

Rules that keep this honest:
  * No way to ask (no confirm callback) means NO. Calling a tool without one can never skip a confirmation.
  * A classifier that crashes, or returns an unknown tier, means NEVER.
  * If a call would only produce an error, it is classed auto: nothing is asked for something that cannot happen.
  * Exfiltration guard: once a tool that reads files or runs commands has run (the chat is "tainted"),
    web_search asks first, showing the exact query, until /reset. A poisoned file cannot quietly send text out.
  * Nothing is destroyed: edit/overwrite keep the old version and delete moves the file to <workspace>/.trash/.
  * File tools see only the workspace folder. The terminal tool starts in it, but a shell is NOT confined to
    it: the confirmation and the blocklist are the protection, not a sandbox (a bubblewrap sandbox is planned).

To add a tool: write a function that takes plain arguments and returns a short string, then add a Tool(...)
entry in build_toolbox(). Return text that is easy to say aloud, and keep it short.
"""
import datetime
from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from typing import Callable
import urllib.parse

MAX_RESULT_CHARS = 6000
MAX_WRITE_CHARS = 200_000
TRASH = ".trash"

AUTO, CONFIRM, NEVER = "auto", "confirm", "never"
TIERS = (AUTO, CONFIRM, NEVER)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict           # JSON schema of the arguments
    fn: Callable[..., str]
    filler: str = "generic"    # which filler set plays if this tool is slow
    tier: str = AUTO
    classify: Callable[[dict], "str | tuple[str, str]"] | None = None   # per call: tier, or (tier, reason)
    describe: Callable[[dict], str] | None = None                       # the confirmation sentence
    taints: bool = False            # its output can hold private or untrusted text (files, command output)
    ask_when_tainted: bool = False          # an outward channel: asks (instead of auto) once the chat is tainted
    label: str = ""         #short name for the settings page
    blurb: str = ""         # one phrase for the prompt's tool summary
    power: bool = False         # enabling it raises what Sage can do on the machine


@dataclass
class ToolResult:
    text: str
    tier: str = AUTO
    outcome: str = "ran"       # ran | error | denied (user said no / nobody to ask) | blocked | unknown
    confirm_wait: float = 0.0  # seconds spent waiting for the user's answer (not tool time)


# confirm(tool, kwargs, summary) -> bool
Confirm = Callable[[Tool, dict, str], bool]


class Toolbox:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}
        self.tainted = False        # a tool that reads files or runs commands has run since the last reset

    def reset_taint(self) -> None:
        self.tainted = False

    def __bool__(self):
        return bool(self.tools)

    def get(self, name):
        return self.tools.get(name)

    def schemas(self):
        return [{"type": "function",
                 "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in self.tools.values()]

    def call(self, name: str, arguments: str, confirm: Confirm | None = None) -> str:
        return self.run(name, arguments, confirm).text

    def run(self, name: str, arguments: str, confirm: Confirm | None = None) -> ToolResult:
        """Checks the tier, asks if needed, runs the tool. The only door to a tool function."""
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(f"Error: there is no tool called {name}.", outcome="unknown")
        try:
            kwargs = json.loads(arguments) if arguments and arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return ToolResult(f"Error: the arguments for {name} are not valid JSON: {exc}", outcome="error")
        if not isinstance(kwargs, dict):
            return ToolResult("Error: tool arguments must be a JSON object.", outcome="error")

        try:
            verdict = tool.classify(kwargs) if tool.classify else tool.tier
            tier, reason = verdict if isinstance(verdict, tuple) else (verdict, "")
        except Exception as exc:  # noqa: BLE001  - fail closed
            return ToolResult(f"Refused: I could not check that {name} is safe to run ({exc}).", NEVER, "blocked")
        if tier not in TIERS:
            tier = NEVER
        if tier == NEVER:
            return ToolResult(f"Refused: {reason or 'that action is not allowed'}. Do not try another way around it.",
                              NEVER, "blocked")

        if tier == AUTO and tool.ask_when_tainted and self.tainted:
            tier = CONFIRM             # e.g. web_search after read_file: the query could carry file text out

        waited = 0.0
        if tier == CONFIRM:
            if confirm is None:
                return ToolResult("Refused: that needs the user's confirmation and there is no way to ask right "
                                  "now. Do not retry.", CONFIRM, "denied")
            try:
                summary = tool.describe(kwargs) if tool.describe else f"{name} {json.dumps(kwargs)}"
            except Exception:  # noqa: BLE001
                summary = f"{name} {json.dumps(kwargs)}"
            t = time.monotonic()
            try:
                approved = bool(confirm(tool, kwargs, summary))
            except Exception:  # noqa: BLE001  - any failure of the asking machinery is a no
                approved = False
            waited = time.monotonic() - t
            if not approved:
                return ToolResult("The user said no, so nothing was done. Do not try it again or find another "
                                  "way. Just say it is cancelled.", CONFIRM, "denied", waited)

        outcome = "ran"
        if tool.taints:
            self.tainted = True        # set before running: even an error message can echo file text
        try:
            out = str(tool.fn(**kwargs))
        except TypeError as exc:
            out, outcome = f"Error: wrong arguments for {name}: {exc}", "error"
        except Exception as exc:  # noqa: BLE001
            out, outcome = f"Error: {name} failed: {exc}", "error"
        if out.startswith("Error"):
            outcome = "error"
        if len(out) > MAX_RESULT_CHARS:
            out = out[:MAX_RESULT_CHARS] + "\n[output cut]"
        return ToolResult(out, tier, outcome, waited)


# --------------------------------------------------------------------------- simple tools
def get_time() -> str:
    now = datetime.datetime.now().astimezone()
    return now.strftime("%A, %d %B %Y, %H:%M (%Z, UTC%z)")


# --------------------------------------------------------------------------- web tools
def web_search(query: str, max_results: int = 5) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: the search package is not installed. Run: uv pip install ddgs"
    max_results = max(1, min(int(max_results), 8))
    results = DDGS(timeout=10).text(query, max_results=max_results)
    if not results:
        return "No results found."
    lines = []
    for r in results:
        body = (r.get("body") or "").strip().replace("\n", " ")
        lines.append(f"- {r.get('title', '').strip()}: {body[:300]} ({r.get('href', '')})")
    return "\n".join(lines)


class _HTMLTextExtractor(HTMLParser):
    """Lightweight stdlib HTML-to-text extractor that drops scripts, styles, and nav chrome."""
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "nav", "footer", "header", "aside"}
    _BLOCK_TAGS = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "pre", "tr", "br"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and t in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and t in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            cleaned = " ".join(data.split())
            if cleaned:
                self._parts.append(cleaned + " ")

    def get_text(self) -> str:
        raw = "".join(self._parts)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return "\n".join(lines)


def _check_public_url(url: str) -> str:
    """Validates that url is http/https and does not resolve to localhost or a private LAN IP."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http and https URLs are allowed (scheme not allowed)")
    host = parsed.hostname
    if not host:
        raise ValueError("missing hostname in URL")
    if host.lower() in ("localhost", "localhost.localdomain") or host.lower().endswith(".local"):
        raise ValueError("local network URLs are not allowed")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve {host}: {exc}") from exc
    for info in infos:
        ip_str = info[4][0]
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError("accessing private or loopback IP addresses is not allowed")
    return url.strip()


def read_webpage(url: str, max_chars: int = 4000) -> str:
    import requests

    try:
        safe_url = _check_public_url(url)
    except ValueError as exc:
        return f"Error: {exc}."
    max_chars = max(500, min(int(max_chars), MAX_RESULT_CHARS))
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) SageAssistant/1.0"}
    try:
        with requests.get(safe_url, headers=headers, timeout=10, stream=True) as r:
            if r.status_code != 200:
                return f"Error: server returned HTTP {r.status_code}."
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text/" not in ctype and "json" not in ctype and "xml" not in ctype:
                return f"Error: unsupported content type ({ctype or 'binary'})."
            chunks, size = [], 0
            for chunk in r.iter_content(chunk_size=16384):
                chunks.append(chunk)
                size += len(chunk)
                if size >= 524_288:         # Download capped at 512 KB
                    break
            html = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
    except requests.RequestException as exc:
        return f"Error: could not fetch webpage: {exc}"

    if "html" not in ctype:
        text = html.strip()
    else:
        parser = _HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
    if not text:
        return "No readable text found on that page."
    return text[:max_chars] + ("\n[page cut]" if len(text) > max_chars else "")


# --------------------------------------------------------------------------- clipboard tools
def get_clipboard(max_chars: int = 4000) -> str:
    max_chars = max(100, min(int(max_chars), MAX_RESULT_CHARS))
    if shutil.which("wl-paste"):
        cmd = ["wl-paste", "--no-newline"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    else:
        return "Error: no clipboard utility found. Install wl-clipboard (sudo pacman -S wl-clipboard)."
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=3, check=False)
    except subprocess.TimeoutExpired:
        return "Error: reading the clipboard timed out."
    except OSError as exc:
        return f"Error: could not read clipboard: {exc}"
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace").strip().lower()
        if "nothing is copied" in err or not res.stdout:
            return "The clipboard is empty."
        return f"Error: clipboard read failed ({err or res.returncode})."
    if b"\x00" in res.stdout[:4096]:
        return "Error: the clipboard currently holds binary data (such as an image), not text."
    text = res.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        return "The clipboard is empty."
    return text[:max_chars] + ("\n[clipboard cut]" if len(text) > max_chars else "")


def set_clipboard(text: str) -> str:
    if not text:
        return "Error: text to copy must not be empty."
    if len(text) > MAX_WRITE_CHARS:
        return f"Error: text is too long for the clipboard (limit {MAX_WRITE_CHARS} characters)."
    if shutil.which("wl-copy"):
        cmd = ["wl-copy", "--"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard", "-i"]
    else:
        return "Error: no clipboard utility found. Install wl-clipboard (sudo pacman -S wl-clipboard)."
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
             close_fds=True,
        )
        if proc.stdin is not None:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Error: copying to the clipboard timed out."
    except OSError as exc:
        return f"Error: could not write to clipboard: {exc}"
    if proc.returncode != 0:
        return f"Error: clipboard write failed (exit code {proc.returncode})."
    return f"Copied {len(text)} characters to the clipboard."

# --------------------------------------------------------------------------- file tools
def _safe_path(root: Path, rel: str) -> Path:
    """Resolves rel inside root (following symlinks) or raises."""
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError("that path is outside the workspace folder I am allowed to use")
    return p


def _writable_path(root: Path, rel: str) -> Path:
    """Like _safe_path, but also keeps the tools out of the trash folder and off the workspace root itself."""
    p = _safe_path(root, rel)
    if p == root:
        raise ValueError("that is the workspace folder itself, give a file path")
    if p == root / TRASH or (root / TRASH) in p.parents:
        raise ValueError("that is the trash folder, which I do not touch")
    return p


def _keep_old(root: Path, p: Path, move: bool) -> str:
    """Puts the current version of p into <root>/.trash/<timestamp>/<same relative path>. Returns that path."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rel = p.relative_to(root)
    dest = root / TRASH / stamp / rel
    n = 1
    while dest.exists():
        dest = root / TRASH / f"{stamp}-{n}" / rel
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    (shutil.move if move else shutil.copy2)(str(p), str(dest))
    return str(dest.relative_to(root))


def _clip(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


def make_file_tools(root: Path):
    """Returns the file tools as a dict of Tool objects. All of them are confined to root."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    def rel(p: Path) -> str:
        return str(p.relative_to(root))

    def list_files(path: str = ".") -> str:
        p = _safe_path(root, path)
        if not p.is_dir():
            return "Error: not a folder."
        entries = [e for e in p.iterdir() if not (p == root and e.name == TRASH)]
        entries = sorted(entries, key=lambda e: (not e.is_dir(), e.name.lower()))[:100]
        if not entries:
            return "The folder is empty."
        return "\n".join(f"{e.name}/" if e.is_dir() else f"{e.name} ({e.stat().st_size} bytes)" for e in entries)

    def read_file(path: str, max_chars: int = 4000) -> str:
        p = _safe_path(root, path)
        if not p.is_file():
            return "Error: no such file."
        data = p.read_bytes()[: int(max_chars) * 4]
        if b"\x00" in data:
            return "Error: that looks like a binary file."
        text = data.decode("utf-8", errors="replace")
        return text[: int(max_chars)] + ("\n[file cut]" if len(text) > int(max_chars) else "")

    # ---- write_file: new file = auto, replacing an existing one = confirm
    def write_file(path: str, content: str, overwrite: bool = False) -> str:
        p = _writable_path(root, path)
        if len(content) > MAX_WRITE_CHARS:
            return f"Error: that is too long to write in one go (limit {MAX_WRITE_CHARS} characters)."
        if p.is_dir():
            return "Error: that path is a folder."
        note = ""
        if p.exists():
            if not overwrite:
                return "Error: that file already exists. Use edit_file to change it, or set overwrite to true."
            note = f" The old version is in {_keep_old(root, p, move=False)}."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {rel(p)} ({len(content)} characters).{note}"

    def write_classify(kw: dict):
        try:
            p = _writable_path(root, kw["path"])
        except (ValueError, KeyError, TypeError, OSError):
            return AUTO                       # write_file will just return the error
        return CONFIRM if (p.is_file() and kw.get("overwrite")) else AUTO

    def write_describe(kw: dict) -> str:
        p = _writable_path(root, kw["path"])
        return (f"Overwrite {rel(p)} ({p.stat().st_size} bytes -> {len(kw.get('content', ''))} characters). "
                f"The old version goes to the trash folder.")

    # ---- edit_file: exact text replacement, always confirm
    def plan_edit(path: str, old: str, new: str):
        p = _writable_path(root, path)
        if not p.is_file():
            raise ValueError("no such file")
        if not old:
            raise ValueError("old must be the exact text to replace, not empty (use write_file for new content)")
        text = p.read_text(encoding="utf-8", errors="strict")
        n = text.count(old)
        if n == 0:
            raise ValueError("that exact text is not in the file (check spaces and line breaks)")
        if n > 1:
            raise ValueError(f"that text appears {n} times; include more surrounding text so it matches once")
        if len(text) - len(old) + len(new) > MAX_WRITE_CHARS:
            raise ValueError("the result would be too long")
        return p, text, n

    def edit_file(path: str, old: str, new: str) -> str:
        try:
            p, text, _ = plan_edit(path, old, new)
        except UnicodeDecodeError:
            return "Error: that file is not plain UTF-8 text."
        except ValueError as exc:
            return f"Error: {exc}."
        kept = _keep_old(root, p, move=False)
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {rel(p)}. The old version is in {kept}."

    def edit_classify(kw: dict):
        try:
            plan_edit(kw["path"], kw["old"], kw["new"])
        except (ValueError, KeyError, TypeError, OSError):
            return AUTO                       # edit_file will return the error without touching anything
        return CONFIRM

    def edit_describe(kw: dict) -> str:
        p = _writable_path(root, kw["path"])
        return f"Edit {rel(p)}: replace \"{_clip(kw['old'])}\" with \"{_clip(kw['new'])}\". The old version goes to the trash folder."

    # ---- delete_file: move to trash, always confirm
    def delete_file(path: str) -> str:
        p = _writable_path(root, path)
        if p.is_dir():
            return "Error: that is a folder; I only delete single files."
        if not p.is_file():
            return "Error: no such file."
        return f"Moved {rel(p)} to {_keep_old(root, p, move=True)}. It can be restored from there."

    def delete_classify(kw: dict):
        try:
            return CONFIRM if _writable_path(root, kw["path"]).is_file() else AUTO
        except (ValueError, KeyError, TypeError, OSError):
            return AUTO

    def delete_describe(kw: dict) -> str:
        p = _writable_path(root, kw["path"])
        return f"Delete {rel(p)} ({p.stat().st_size} bytes). It moves to the trash folder, not gone for good."

    path_prop = {"type": "string", "description": "file path inside the workspace"}
    return {t.name: t for t in [
        Tool("list_files", "List the files and folders in the user's workspace folder.",
             {"type": "object",
              "properties": {"path": {"type": "string", "description": "folder inside the workspace, default '.'"}}},
             list_files, "system", taints=True),
        Tool("read_file", "Read a text file from the user's workspace folder.",
             {"type": "object",
              "properties": {"path": path_prop, "max_chars": {"type": "integer", "description": "default 4000"}},
              "required": ["path"]}, read_file, "system", taints=True),
        Tool("write_file",
             "Create a new text file in the workspace folder (folders are created as needed). It refuses to replace "
             "an existing file unless overwrite is true, which asks the user first.",
             {"type": "object",
              "properties": {"path": path_prop, "content": {"type": "string", "description": "the full file text"},
                             "overwrite": {"type": "boolean", "description": "replace an existing file, default false"}},
              "required": ["path", "content"]},
             write_file, "system", classify=write_classify, describe=write_describe),
        Tool("edit_file",
             "Change a workspace file by replacing one exact piece of text with another. The text to replace must "
             "appear exactly once. The user is asked to confirm.",
             {"type": "object",
              "properties": {"path": path_prop, "old": {"type": "string", "description": "exact existing text"},
                             "new": {"type": "string", "description": "replacement text"}},
              "required": ["path", "old", "new"]},
             edit_file, "system", classify=edit_classify, describe=edit_describe),
        Tool("delete_file",
             "Delete one workspace file (it is moved to a trash folder, not erased). The user is asked to confirm.",
             {"type": "object", "properties": {"path": path_prop}, "required": ["path"]},
             delete_file, "system", classify=delete_classify, describe=delete_describe),
    ]}


# --------------------------------------------------------------------------- terminal
# Commands that run without asking: harmless, read-only, and only when every path argument stays inside the
# workspace. They run without a shell, so nothing in the arguments can be interpreted.
AUTO_COMMANDS_NO_PATH = {"pwd", "whoami", "uname", "uptime", "free", "df"}
AUTO_COMMANDS_PATH = {"ls", "cat", "head", "tail", "wc", "du"}

# Refused outright. This is a seatbelt against a model's mistakes, NOT a security boundary: a determined
# command can be disguised. The real protection is that everything else asks the user first.
_NEVER = [
    (r"\b(sudo|doas|pkexec|su)\b", "commands with elevated privileges are not allowed"),
    (r"\b(mkfs(\.\w+)?|wipefs|fdisk|parted|sfdisk|shred|dd)\b", "disk and partition tools are not allowed"),
    (r"\b(shutdown|reboot|poweroff|halt)\b", "shutting down or rebooting is not allowed"),
    (r"\bsystemctl\b[^;&|]*\b(poweroff|reboot|halt|suspend|hibernate)\b", "power commands are not allowed"),
    (r":\s*\(\s*\)\s*\{", "that looks like a fork bomb"),
    (r"(curl|wget|fetch)\b[^;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", "piping a download into a shell is not allowed"),
    (r">\s*/dev/(sd|nvme|mmcblk|disk|hd)", "writing to a disk device is not allowed"),
    (r"\b(chmod|chown|chgrp)\b(?=[^;&|]*\s-\w*R)[^;&|]*\s(/|~|\$HOME)/?\*?(\s|$)",
     "recursive permission changes on system folders are not allowed"),
]
_SHELL_META = re.compile(r"[|;&<>`$(){}*?\[\]\\!\n~#]")


def _is_dangerous_rm(tokens: list[str]) -> bool:
    """rm anywhere in the command aimed outside the workspace (absolute, ~, .., $HOME, bare * or /)."""
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) != "rm":
            continue
        for arg in tokens[i + 1:]:
            if arg.startswith("-"):
                continue
            if arg in ("*", "/", "..", ".") or arg.startswith(("/", "~", "../", "$")) or ".." in arg.split("/"):
                return True
    return False


def classify_command(command: str, root: Path) -> tuple[str, str]:
    """(tier, reason) for a shell command. See the module docstring for the rules."""
    cmd = command.strip()
    if not cmd:
        return AUTO, ""                        # run_command returns the error
    for pattern, why in _NEVER:
        if re.search(pattern, cmd, re.IGNORECASE):
            return NEVER, why
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = None                          # unbalanced quotes: let the user see it, bash will complain
    if _is_dangerous_rm(re.split(r"[\s;&|()]+", cmd)) or (tokens and _is_dangerous_rm(tokens)):
        return NEVER, "deleting outside the workspace folder is not allowed"
    if tokens is None or not tokens or _SHELL_META.search(cmd):
        return CONFIRM, ""                     # pipes, redirects, globs, substitutions: needs a shell, ask first
    head, args = tokens[0], tokens[1:]
    if head in AUTO_COMMANDS_NO_PATH:
        return (AUTO, "") if all(re.fullmatch(r"-[A-Za-z]+", a) for a in args) else (CONFIRM, "")
    if head in AUTO_COMMANDS_PATH:
        for a in args:
            if a.startswith("-"):
                if re.fullmatch(r"-[A-Za-z0-9]+", a):
                    continue                   # plain short flags only: -l, -la, -n5
                return CONFIRM, ""             # --long=value, --files0-from=..., "--", "-": could name outside files
            try:
                _safe_path(root, a)
            except (ValueError, OSError):
                return CONFIRM, ""             # touches something outside the workspace: ask
        return AUTO, ""
    return CONFIRM, ""


def make_terminal_tool(root: Path, default_timeout: int = 15, max_timeout: int = 60, max_output: int = 4000) -> Tool:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # No inherited secrets; HOME points at the workspace so "~" and tool config land there, not in the real home.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root), "LANG": "C.UTF-8", "TERM": "dumb"}
    limit = "ulimit -f 20480; "                # no file (including captured output) can pass 20 MB

    def run_command(command: str, timeout: int = default_timeout) -> str:
        tier, why = classify_command(command, root)    # again, in case anything ever calls this directly
        if tier == NEVER:
            return f"Refused: {why}."
        if not command.strip():
            return "Error: empty command."
        timeout = max(1, min(int(timeout), max_timeout))
        if tier == AUTO:
            argv = ["bash", "-c", limit + 'exec "$@"', "_", *shlex.split(command)]
        else:
            argv = ["bash", "-c", limit + command]
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)   # the whole group: the shell and anything it started
                except ProcessLookupError:
                    pass
                proc.wait()
            out.seek(0)
            raw = out.read(max_output + 1)
        text = raw[:max_output].decode("utf-8", errors="replace").rstrip()
        if len(raw) > max_output:
            text += "\n[output cut]"
        if timed_out:
            text += f"\n[stopped: it ran longer than {timeout} s]"
        elif proc.returncode != 0:
            text += f"\n[exit code {proc.returncode}]"
        return text or "(no output)"

    def classify(kw: dict):
        return classify_command(str(kw.get("command", "")), root)

    def describe(kw: dict) -> str:
        return f"Run in the workspace folder: {str(kw.get('command', '')).strip()[:500]}"

    return Tool(
        "run_command",
        "Run a shell command in the user's workspace folder and return its output. Harmless read-only commands "
        "(ls, cat, head, tail, wc, du, pwd, uname, df, free, uptime, whoami) run directly; anything else asks the "
        "user first, and dangerous commands are refused. Commands are stopped after the timeout (default 15 s, "
        "at most 60).",
        {"type": "object",
         "properties": {"command": {"type": "string", "description": "the shell command"},
                        "timeout": {"type": "integer", "description": "seconds, default 15, at most 60"}},
         "required": ["command"]},
        run_command, "system", classify=classify, describe=describe, taints=True)


# --------------------------------------------------------------------------- toolbox
def ask_coder(task: str) -> str:
    return "The coder model is not connected yet, so I could not run this. Tell the user it is not available yet."


TOOL_INFO = {
    "get_time": ("Date and time", "check time and date", False),
    "web_search": ("Web search", "search the web", False),
    "read_webpage": ("Read web pages", "read a webpage", False),
    "get_clipboard": ("Read clipboard", "read the clipboard", False),
    "set_clipboard": ("Write clipboard", "copy text to the clipboard", False),
    "list_files": ("List files", "list all the files in a directory", False),
    "read_file": ("Read files", "read the contents of a file", False),
    "write_file": ("Create files", "create files", False),
    "edit_file": ("Edit files", "edit files", False),
    "delete_file": ("Delete files", "delete files", False),
    "run_command": ("Run commands", "run commands", True),
}

DEFAULT_TOOLS = (
    "get_time,web_search,read_webpage,get_clipboard,set_clipboard,"
    "list_files,read_file,write_file,edit_file,delete_file"
)


def build_toolbox(enabled: str, files_root: Path) -> Toolbox:
    """enabled: comma separated tool names, 'all', or 'none'. run_command is never on by default."""
    file_tools = make_file_tools(files_root)
    every = [
        Tool("get_time", "Get the current local date and time.",
             {"type": "object", "properties": {}}, get_time, "generic"),
        Tool("web_search",
             "Search the web for current information, news, prices, or facts you are unsure about. "
             "Returns short result snippets.",
             {"type": "object",
              "properties": {"query": {"type": "string", "description": "the search query"},
                             "max_results": {"type": "integer", "description": "1 to 8, default 5"}},
              "required": ["query"]}, web_search, "search", ask_when_tainted=True,
             describe=lambda kw: (f"Search the web for \"{_clip(str(kw.get('query', '')), 120)}\". This chat has read "
                                  f"your files or run commands, so check the search holds nothing private.")),
        Tool("read_webpage",
             "Fetch a web page URL and read its plain text content. Use this when web_search snippets are not enough.",
             {"type": "object",
              "properties": {"url": {"type": "string", "description": "the http or https URL to read"},
                             "max_chars": {"type": "integer", "description": "default 4000"}},
              "required": ["url"]}, read_webpage, "search", taints=True, ask_when_tainted=True,
             describe=lambda kw: (f"Open web page \"{_clip(str(kw.get('url', '')), 120)}\". This chat has read "
                                  f"your files or run commands, so check the URL holds nothing private.")),
        Tool("get_clipboard",
             "Read the current text from the user's system clipboard.",
             {"type": "object",
              "properties": {"max_chars": {"type": "integer", "description": "default 4000"}}},
             get_clipboard, "system", taints=True),
        Tool("set_clipboard",
             "Copy text into the user's system clipboard.",
             {"type": "object",
              "properties": {"text": {"type": "string", "description": "the text to copy"}},
              "required": ["text"]},
             set_clipboard, "system"),
        *file_tools.values(),
        make_terminal_tool(files_root),
        Tool("ask_coder", "Hand a programming task to the coding model. (Placeholder: not connected yet.)",
             {"type": "object",
              "properties": {"task": {"type": "string", "description": "what to build or fix"}},
              "required": ["task"]}, ask_coder, "coder"),
    ]
    for t in every:
        if t.name in TOOL_INFO:
            t.label, t.blurb, t.power = TOOL_INFO[t.name]
    names = {t.name for t in every}
    mode = enabled.strip().lower()
    if mode == "none":
        return Toolbox([])
    wanted = names if mode == "all" else {n.strip() for n in enabled.split(",") if n.strip()}
    wanted = wanted or set(DEFAULT_TOOLS.split(","))
    unknown = wanted - names
    if unknown:
        raise SystemExit(f"Unknown tool(s): {', '.join(sorted(unknown))}. Available: {', '.join(t.name for t in every)}")
    return Toolbox([t for t in every if t.name in wanted])

def tool_catalog(files_root: Path) -> list[dict]:
    """Every tool the settings page can offer, with what it needs to show: label, tier badge, power flag, default."""
    tools = build_toolbox("all", files_root).tools.values()
    on = set(DEFAULT_TOOLS.split(","))
    return [{"name": t.name, "label": t.label, "description": t.blurb[:1].upper() + t.blurb[1:],
             "tier": "mixed" if (t.classify or t.ask_when_tainted) else t.tier,
             "power": t.power, "default_on": t.name in on}
            for t in tools if t.blurb]