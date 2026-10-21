"""Text for speech: cleaning and the chunker that cuts a token stream into speakable pieces."""
import re

TAGS = ("chuckle", "laugh", "sigh")
TAG_RE = re.compile(r"\[(?:%s)\]" % "|".join(TAGS))
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
SENT_RE = re.compile(r'[.!?…]+["\')\]]*\s+|\n+')
SOFT_RE = re.compile(r"[,;:—–]\s")


def n_words(s: str) -> int:
    return len(s.split())


# --------------------------------------------------------------------------- text -> speakable chunks
def clean_for_speech(text: str, keep_tags: bool) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)       # markdown links -> link text
    text = re.sub(r"https?://\S+", "link", text)
    text = EMOJI_RE.sub("", text)
    text = re.sub(r"^\s*[-•>]+\s+", "", text)               # bullets / quotes at line start
    text = re.sub(r"[*#`~|]+", "", text).replace("_", " ")
    if not keep_tags:
        text = TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class Chunker:
    """Turns a stream of text deltas into speakable chunks. Drops fenced code blocks."""

    def __init__(self, first_cut_words: int = 6, min_words: int = 3, split_words: int = 12):
        self.first_cut_words = first_cut_words
        self.min_words = min_words
        self.split_words = split_words   # a short sentence is not glued to a long one past this many words
        self.fbuf = ""      # raw text not yet checked for ``` fences
        self.buf = ""       # speakable text waiting for a sentence end
        self.carry = ""     # finished sentences too short to speak alone
        self.in_code = False
        self.first_done = False

    def _filter_code(self, final: bool = False) -> str:
        out = []
        while True:
            if self.in_code:
                i = self.fbuf.find("```")
                if i < 0:
                    self.fbuf = "" if final else self.fbuf[-2:]
                    break
                self.fbuf = self.fbuf[i + 3:]
                self.in_code = False
            else:
                i = self.fbuf.find("```")
                if i < 0:
                    if final:
                        out.append(self.fbuf)
                        self.fbuf = ""
                    else:
                        keep = min(len(self.fbuf) - len(self.fbuf.rstrip("`")), 2)
                        out.append(self.fbuf[:len(self.fbuf) - keep])
                        self.fbuf = self.fbuf[len(self.fbuf) - keep:]
                    break
                out.append(self.fbuf[:i] + "\n")
                self.fbuf = self.fbuf[i + 3:]
                self.in_code = True
        return "".join(out)

    def _extract(self) -> list:
        out = []
        while True:
            m = SENT_RE.search(self.buf)
            if not m:
                break
            sent, self.buf = self.buf[:m.end()], self.buf[m.end():]
            if self.carry and n_words(self.carry) + n_words(sent) > self.split_words:
                # Short sentence followed by a long one: speak the short one now (it is a whole sentence),
                # instead of waiting for the long one and starting late.
                out.append(self.carry)
                self.carry = ""
                self.first_done = True
            self.carry = (self.carry + " " + sent).strip()
            if n_words(self.carry) >= self.min_words:
                out.append(self.carry)
                self.carry = ""
                self.first_done = True
        if not self.first_done and not out:
            # Long first sentence (possibly after a short one): speak up to the first comma once there are
            # enough words.
            for m in SOFT_RE.finditer(self.buf):
                if n_words(self.carry) + n_words(self.buf[:m.start()]) >= self.first_cut_words:
                    out.append((self.carry + " " + self.buf[:m.start() + 1]).strip())
                    self.carry = ""
                    self.buf = self.buf[m.end():]
                    self.first_done = True
                    break
        if len(self.buf) > 280:  # runaway sentence with no punctuation
            cut = self.buf.rfind(" ", 0, 280)
            cut = cut if cut > 0 else 280
            out.append(self.buf[:cut])
            self.buf = self.buf[cut:].lstrip()
            self.first_done = True
        return out

    def feed(self, delta: str) -> list:
        self.fbuf += delta
        self.buf += self._filter_code()
        return self._extract()

    def flush(self) -> list:
        self.buf += self._filter_code(final=True)
        out = self._extract()
        rest = (self.carry + " " + self.buf).strip()
        self.carry = self.buf = ""
        if rest:
            out.append(rest)
        return out
