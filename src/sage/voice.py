"""The voice engine: text chunks in, audio out, with pre-buffer hold, filler clips and speed estimates.

Two worker threads (TTS and playback) share queues; `gen` numbers every reply so stale work is dropped."""
import queue
import random
import re
import threading
import time

import numpy as np

from sage.console import REAL_STDERR, quiet
from sage.text import clean_for_speech, n_words


# --------------------------------------------------------------------------- the voice engine
class Voice:
    def __init__(self, backend, sink, filler_pause: float = 1.0, max_hold: float = 2.0,
                 gap_tolerance: float = 0.3, min_filler_wait: float = 1.2, wait_filler_pause: float = 0.4):
        self.backend, self.sink = backend, sink
        self.filler_pause = filler_pause
        self.wait_filler_pause = wait_filler_pause  # shorter pause after a "Hmm.": the answer is already coming
        self.pause_s = filler_pause         # pause that follows the filler now playing
        self.min_filler_wait = min_filler_wait  # a waiting-filler only plays if speech is still this far away
        self.speaking = False               # real speech is playing (or about to)
        self.on_speech_start = None         # called (from the player thread) as each chunk of REAL speech begins,
                                            # not for fillers: the network server turns it into the "speaking" state
        self.holding = False                # first chunk is ready but waiting (pre-buffer hold)
        self.hold_first_dur = 0.0
        self.hold_start = 0.0
        self.inline_clip = None             # filler to play from inside the hold loop
        self.filler_count = 0               # fillers played in this reply
        self.max_hold = max_hold            # longest wait before speech starts, to avoid gaps (0 = off)
        self.gap_tolerance = gap_tolerance  # predicted gaps shorter than this are ignored
        self.q_text, self.q_audio = queue.Queue(), queue.Queue()
        self.lock = threading.Lock()
        self.gen = 0
        self.t0 = time.monotonic()
        self.done = threading.Event()
        self.stats = {}
        self.play_end = None
        self.hold_until = None              # after a filler: real speech waits until this time
        self.real_started = False           # has the first real chunk of this reply started?
        self.spoke = False                  # has real speech audio been produced for this reply?
        self.pending = 0                    # text chunks queued or being synthesized
        self.filler_until = 0.0             # a filler is playing or its pause is running until then
        self.last_filler = None
        self.fillers = {}                   # category -> list of audio arrays
        self.cur = None                     # (words, start time, gen) of the chunk being synthesized
        self.obs = []                       # (words, audio seconds, generation seconds) of recent chunks
        self.est_a, self.est_b, self.est_spw = 0.5, 0.45, 0.37  # start values; adapted from real timings
        threading.Thread(target=self._tts_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    # -- control
    def new_reply(self) -> int:
        with self.lock:
            self.gen += 1
            self._drain()
            self.t0 = time.monotonic()
            self.done = threading.Event()
            self.stats = {"gaps": 0.0, "filler": None, "tools": []}
            self.play_end = self.hold_until = None
            self.real_started = self.spoke = False
            self.speaking = self.holding = False
            self.inline_clip = None
            self.filler_count = 0
            self.pending = 0
            self.filler_until = 0.0
            return self.gen

    def cancel(self):
        with self.lock:
            self.gen += 1
            self._drain()
            self.pending = 0
            self.speaking = self.holding = False
            self.inline_clip = None
            self.done.set()

    def _drain(self):
        for q in (self.q_text, self.q_audio):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def say(self, gen: int, text: str):
        with self.lock:
            if gen != self.gen:
                return              # cancelled: nothing to synthesize
            self.pending += 1
        self.q_text.put((gen, "text", text))

    def end(self, gen: int):
        self.q_text.put((gen, "end", None))

    def wait_done(self):
        while not self.done.wait(0.2):
            pass

    def play_filler(self, gen: int, category: str = "generic") -> bool:
        """Plays a random filler clip from `category`, unless it would be pointless:
        real speech already exists or is being generated, or another filler is still playing.
        Used for slow tool calls (and optionally slow chat replies, category "thinking").
        Real speech that is ready earlier waits until the filler has finished plus a pause."""
        clips = self.fillers.get(category) or self.fillers.get("generic") or []
        if not clips:
            return False
        with self.lock:
            if gen != self.gen or self.spoke or self.pending > 0 or time.monotonic() < self.filler_until:
                return False
            return self._queue_filler(gen, clips, category)

    def _queue_filler(self, gen: int, clips: list, category: str, inline: bool = False) -> bool:
        """Caller holds self.lock."""
        options = [c for c in clips if c is not self.last_filler] or clips
        clip = random.choice(options)
        self.last_filler = clip
        self.pause_s = self.wait_filler_pause if category == "thinking" else self.filler_pause
        self.filler_until = time.monotonic() + len(clip) / self.backend.sr + self.pause_s
        self.filler_count += 1
        self.stats["filler"] = category
        if inline:
            self.inline_clip = clip
        else:
            self.q_audio.put((gen, "filler", clip))
        return True

    def _remaining_wait(self, gen: int):
        """Best guess of how many seconds until speech starts (None = unknown). Caller holds self.lock."""
        now = time.monotonic()
        if self.holding:
            need = self._hold_needed(gen, self.hold_first_dur)
            return max(0.0, min(need, self.max_hold - (now - self.hold_start)))
        if self.spoke:
            return 0.0
        cur = self.cur
        if cur is not None and cur[2] == gen:
            return max(0.0, self._gen_est(cur[0]) - (now - cur[1]))
        with self.q_text.mutex:
            queued = [n_words(clean_for_speech(t, self.backend.supports_tags))
                      for g, k, t in self.q_text.queue if g == gen and k == "text"]
        if queued:
            return self._gen_est(queued[0])
        return None

    def try_wait_filler(self, gen: int, category: str) -> bool:
        """A filler for plain waiting (the model or the TTS is slow), not for a tool. It plays only when speech
        has not started and is still estimated to be at least --filler-min-wait seconds away, so it fills a
        real silence instead of delaying an answer that is about to come. Works while the first chunk is
        being generated, while it is waiting in the pre-buffer hold, and only once per reply."""
        clips = self.fillers.get(category) or self.fillers.get("generic") or []
        if not clips:
            return False
        with self.lock:
            if (gen != self.gen or self.speaking or self.filler_count
                    or time.monotonic() < self.filler_until):
                return False
            if self.spoke and not self.holding:
                return False                      # audio is already on its way to the speaker
            left = self._remaining_wait(gen)
            if left is not None:
                if left < self.min_filler_wait:
                    return False                  # speech is too close: a filler would only delay it
                # Only clips that fit: a clip plus its pause must end before the answer is ready, so a
                # short "Hmm." can play where a long "Hmm, let me see." would delay the answer.
                sr = self.backend.sr
                clips = [c for c in clips if len(c) / sr + self.wait_filler_pause <= left]
                if not clips:
                    return False
            return self._queue_filler(gen, clips, category, inline=self.holding)

    def start_wait_watch(self, gen: int, state: dict) -> threading.Event:
        """Background watcher for slow starts. `state` (shared with run_turn) holds: delay, since (monotonic time
        the wait began), cat (filler set), paused (a tool is running, the tool filler handles that)."""
        stop = threading.Event()

        def run():
            while not stop.is_set() and gen == self.gen and not self.speaking and not self.filler_count:
                if not state["paused"] and time.monotonic() - state["since"] >= state["delay"]:
                    if self.try_wait_filler(gen, state["cat"]):
                        return
                time.sleep(0.1)

        threading.Thread(target=run, daemon=True).start()
        return stop

    # -- speed estimates (used to decide how long to wait before speech starts)
    def observe(self, words: int, audio_s: float, gen_s: float):
        if words < 2 or audio_s <= 0:
            return
        self.obs = (self.obs + [(words, audio_s, gen_s)])[-12:]
        self.est_spw = float(np.mean([a / w for w, a, _ in self.obs]))
        if len(self.obs) >= 2:
            auds = np.array([a for _, a, _ in self.obs])
            gens = np.array([g for _, _, g in self.obs])
            if len(self.obs) >= 4 and auds.max() - auds.min() >= 2.0:
                b, a = np.polyfit(auds, gens, 1)
                self.est_a, self.est_b = float(np.clip(a, 0.0, 1.5)), float(np.clip(b, 0.15, 1.5))
            else:
                self.est_b = float(np.clip(np.median((gens - self.est_a) / auds), 0.15, 1.5))

    def _gen_est(self, words: int) -> float:
        return self.est_a + self.est_b * words * self.est_spw

    def _hold_needed(self, gen: int, first_dur: float) -> float:
        """Seconds speech would have to wait, from now, so that it never runs dry before the reply is generated."""
        sr = self.backend.sr
        now = time.monotonic()
        with self.q_audio.mutex:
            ready = [len(a) / sr for g, k, a in self.q_audio.queue if g == gen and k == "audio"]
        cur = self.cur
        with self.q_text.mutex:
            queued = [n_words(clean_for_speech(t, self.backend.supports_tags))
                      for g, k, t in self.q_text.queue if g == gen and k == "text"]
        avail = first_dur + sum(ready)   # audio we could play before the next unfinished chunk is needed
        cum = need = 0.0                 # cum: estimated time until each unfinished chunk is ready
        pending = []
        if cur is not None and cur[2] == gen:
            pending.append((cur[0], now - cur[1]))
        pending += [(w, 0.0) for w in queued]
        for words, already in pending:
            cum += max(0.0, self._gen_est(words) - already)
            need = max(need, cum - avail)
            avail += words * self.est_spw
        return need

    # -- workers
    def _tts_loop(self):
        self.backend.thread_init()
        while True:
            gen, kind, text = self.q_text.get()
            if gen != self.gen:
                continue
            if kind == "end":
                self.q_audio.put((gen, "end", None))
                continue
            try:
                self._speak(gen, text)
            finally:
                with self.lock:
                    if gen == self.gen:
                        self.pending = max(0, self.pending - 1)

    def _speak(self, gen: int, text: str):
        speech = clean_for_speech(text, self.backend.supports_tags)
        if not speech or not re.search(r"\w", speech):
            return
        words = n_words(speech)
        t_synth = time.monotonic()
        self.cur = (words, t_synth, gen)
        try:
            with quiet():
                audio = self.backend.synth(speech)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[tts error: {exc}]", file=REAL_STDERR)
            return
        finally:
            self.cur = None
        dt = time.monotonic() - t_synth
        self.observe(words, len(audio) / self.backend.sr, dt)
        if "first_chunk" not in self.stats:   # for the latency line: what the first chunk cost
            self.stats["first_chunk"] = f"{words}w cut at {t_synth - self.t0:.2f}s, synth {dt:.2f}s"
        with self.lock:
            if gen != self.gen:
                return
            self.stats.setdefault("first_chunk_ready", time.monotonic() - self.t0)
            self.spoke = True
            self.q_audio.put((gen, "audio", audio))

    def _play_loop(self):
        sr = self.backend.sr
        while True:
            gen, kind, audio = self.q_audio.get()
            if gen != self.gen:
                continue
            if kind == "end":
                self.done.set()
                continue
            if kind == "audio" and self.hold_until is not None:
                # A filler just played: leave a natural pause before the real answer.
                while time.monotonic() < self.hold_until and gen == self.gen:
                    time.sleep(0.02)
                self.hold_until = None
                self.play_end = time.monotonic()    # the pause is intentional, not a gap
                if gen != self.gen:
                    continue
            if kind == "audio" and not self.real_started:
                self.real_started = True
                if self.max_hold > 0:
                    # Start only when the rest of the reply will be generated in time (no mid-speech gaps).
                    t_wait = time.monotonic()
                    first_dur = len(audio) / sr
                    self.hold_first_dur, self.hold_start, self.holding = first_dur, t_wait, True
                    try:
                        while gen == self.gen:
                            clip, self.inline_clip = self.inline_clip, None
                            if clip is not None:
                                # A waiting-filler was requested while we hold: play it here, then pause.
                                self.play_end = None
                                for i in range(0, len(clip), 2400):
                                    if gen != self.gen:
                                        break
                                    self.sink.write(clip[i:i + 2400])
                                pause_until = (time.monotonic()
                                               + self.pause_s * random.uniform(0.8, 1.2))
                                while time.monotonic() < pause_until and gen == self.gen:
                                    time.sleep(0.02)
                                t_wait = self.hold_start = time.monotonic()   # the hold restarts after the filler
                                continue
                            if (self._hold_needed(gen, first_dur) <= self.gap_tolerance
                                    or time.monotonic() - t_wait >= self.max_hold):
                                break
                            time.sleep(0.05)
                    finally:
                        self.holding = False
                    if gen != self.gen:
                        continue
                    held = time.monotonic() - t_wait
                    if held > 0.06:
                        self.stats["held"] = held
                        if self.play_end is not None:
                            self.play_end = time.monotonic()    # intentional wait, not a gap
            now = time.monotonic()
            if kind == "audio":
                self.speaking = True
                self.stats.setdefault("real_start", now - self.t0)   # when the real answer begins, after any filler
                if self.on_speech_start is not None:
                    self.on_speech_start()
            if self.play_end is None:
                self.stats["audio_start"] = now - self.t0
                self.play_end = now
            elif now > self.play_end:
                self.stats["gaps"] += now - self.play_end
            self.play_end = max(now, self.play_end) + len(audio) / sr
            if kind == "filler":
                self.hold_until = self.play_end + self.pause_s * random.uniform(0.8, 1.2)
            for i in range(0, len(audio), 2400):
                if gen != self.gen:
                    break
                self.sink.write(audio[i:i + 2400])
