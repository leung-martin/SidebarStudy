"""Sidebar Study - always-on-top Mandarin flash cards graded by voice.

Speak the pinyin (i.e. say the word in Mandarin) and the app checks it.
Say "pass" (English) to fail the card immediately. The mic is read directly,
so it keeps working while a game has focus.
"""
import glob
import json
import os
import pathlib
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unicodedata
import webbrowser
from tkinter import messagebox, ttk

import numpy as np
import sounddevice as sd
import vosk
from pypinyin import Style, pinyin

import mastery_view
from yoyo_sync import MASTERED, YoyoSync, next_level

def _find_root():
    """The folder holding flashcards/ and models/. As a built exe, look upward from the exe."""
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    d = here
    while True:
        if os.path.isdir(os.path.join(d, "flashcards")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return here
        d = parent


ROOT = _find_root()  # where your progress, settings and sign-in are saved
# The portable single-file exe carries its decks, speech models and icon inside itself
# (unpacked to a temp folder at launch); saved data stays beside the exe so it travels with it.
_bundle = getattr(sys, "_MEIPASS", "")
RES = _bundle if _bundle and os.path.isdir(os.path.join(_bundle, "flashcards")) else ROOT
CARD_DIR = os.path.join(RES, "flashcards")
CN_MODEL = os.path.join(RES, "models", "vosk-model-small-cn-0.22")
EN_MODEL = os.path.join(RES, "models", "vosk-model-small-en-us-0.15")
PROGRESS_FILE = os.path.join(ROOT, "progress.json")
SETTINGS_FILE = os.path.join(ROOT, "settings.json")
YOYO_AUTH_FILE = os.path.join(ROOT, "yoyo_auth.json")    # your YoYo session cookie
YOYO_CACHE_FILE = os.path.join(ROOT, "yoyo_cache.json")  # card ids + answers waiting to sync
MASTERY_FILE = os.path.join(ROOT, "mastery.html")        # explorer page, rebuilt on each open
HISTORY_FILE = os.path.join(ROOT, "progress_history.json")  # one snapshot per day, for the trend charts
YOYO_LOG_FILE = os.path.join(ROOT, "yoyo_log.jsonl")     # receipt of every exchange with YoYo

PASS_WORDS = {"pass", "passed", "past"}
PASS_CONF = 0.65          # min confidence for the English "pass" keyword
SLEEP_CHOICES = [0, 1, 2, 3, 5, 10, 15, 30]  # minutes of silence before the mic turns off (0 = never)
HOTKEY_NAME = "Ctrl+Alt+M"
SPEAK_HOTKEY_NAME = "Ctrl+Alt+H"  # (Ctrl+Alt+P is taken by another program on this PC)
# YoYo's grade buttons, as shortcuts: Good / Not Bad / Forgot
GRADE_HOTKEY_NAMES = {"easy": "Ctrl+Alt+G", "good": "Ctrl+Alt+N", "hard": "Ctrl+Alt+F"}
TTS_RATE = -2             # Windows voice speed, -10..10; a bit slower than normal for learning
TTS_VOLUME = 100
LATIN_GAP_MAX = 4        # hanzi an English word in a card may come out as
GRAMMAR_CONF = 0.8       # min confidence per character for the card-limited recognizer
NEW_PER_ROUND = 25        # new cards per round, like a YoYo deck
HANZI_MAX = 110  # biggest the characters get (points), in a roomy window
COMPARE_GUTTER = 30       # px on the left for the 🎯 / 🎙 row icons when comparing pronunciations
POP_MARGIN = 8           # px above/below the characters, room for the success "pop" to grow into
WIDE_RATIO = 1.5         # window at least this much wider than tall: side-by-side layout
COMPACT_BELOW = 420       # window shorter than this (px): tighter spacing, smaller text
DUE_WAIT_S = 30           # how long to wait for YoYo's due list before using the offline order

BG, CARD, INK, DIM = "#101219", "#1a1d29", "#f5f5fa", "#8b90a8"
TRACK, BORDER, BTN, PANEL = "#2a2e40", "#262a3b", "#252937", "#161925"
VIOLET, PINK, TEAL, BLUE = "#8b6cff", "#ff4f9a", "#22d3c5", "#4d8dff"
GREEN, RED, ORANGE, YELLOW = "#2ee59d", "#ff5470", "#ffa53d", "#ffd84d"
HANZI_FONT = "Microsoft YaHei UI"


# ---------------------------------------------------------------- pinyin utils

TONE_MARKS = {}
for base, marks in {"a": "āáǎà", "e": "ēéěè", "i": "īíǐì", "o": "ōóǒò",
                    "u": "ūúǔù", "v": "ǖǘǚǜ"}.items():
    for t, ch in enumerate(marks, 1):
        TONE_MARKS[ch] = (base, t)


def normalize_pinyin(text):
    """Return (toneless letters, tone sequence) for a tone-marked pinyin string."""
    text = unicodedata.normalize("NFC", text.lower())
    text = text.replace("u:", "v").replace("ü", "v")
    letters, tones = [], []
    for ch in text:
        if ch in TONE_MARKS:
            base, t = TONE_MARKS[ch]
            letters.append(base)
            tones.append(t)
        elif "a" <= ch <= "z":
            letters.append(ch)
    return "".join(letters), tones


TONE_NAMES = {1: ("1st", "high"), 2: ("2nd", "rising"), 3: ("3rd", "dipping"), 4: ("4th", "falling")}


def tone_name(syllable, long=True):
    tones = normalize_pinyin(syllable)[1]
    num, shape = TONE_NAMES.get(tones[0], ("neutral", "")) if tones else ("neutral", "")
    return f"{num}, {shape}" if long and shape else num


def hanzi_to_pinyin(text):
    syllables = [s[0] for s in pinyin(text, style=Style.TONE, errors="ignore")]
    # erhua: 哪儿 is written "nǎr", not "nǎ ér"
    for i, ch in enumerate(text):
        if ch == "儿" and i > 0 and i < len(syllables):
            syllables[i - 1] += "r"
            syllables[i] = ""
    return " ".join(s for s in syllables if s)


def blend(c1, c2, t):
    """Mix two #rrggbb colors; t=0 gives c1, t=1 gives c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def hanzi_units(text):
    """Per-character (hanzi, toneless letters, tones, display pinyin) for the hanzi in text."""
    chars = strip_hanzi(text)
    out = []
    for ch, syl in zip(chars, pinyin(chars, style=Style.TONE, errors="ignore")):
        letters, tones = normalize_pinyin(syl[0])
        out.append((ch, letters, tones, syl[0]))
    return out


def card_units(card):
    """Per-character units for a card: (simplified, traditional, letters, tones, display)."""
    simp, trad = strip_hanzi(card["simplified"]), strip_hanzi(card["traditional"])
    if len(trad) != len(simp):
        trad = simp
    first_alt = card["pinyin"].split("/")[0]
    tokens = [t for t in re.split(r"[^\wÀ-ɏ]+", first_alt.lower()) if t]
    if len(tokens) == len(simp):  # the card's own pinyin, one token per character
        syls = [normalize_pinyin(t) + (t,) for t in tokens]
    else:  # joined syllables / erhua / extra latin: fall back to pypinyin
        syls = [u[1:] for u in hanzi_units(simp)]
    return [(s, t) + syl for s, t, syl in zip(simp, trad, syls)]


SANDHI_CHARS = {"一", "不"}


def optional_units(card):
    """Card character indices you may leave out: anything in brackets, like 办事(儿), and
    erhua 儿 (哪儿 nǎr): the R sound is optional, and the recognizer often drops it anyway.
    A 儿 the pinyin spells as its own syllable (儿子 ér zi) is not optional."""
    text = card["simplified"]
    tokens = re.split(r"[^\wÀ-ɏ]+", unicodedata.normalize("NFC", card["pinyin"].lower()))
    er_syllable = any(normalize_pinyin(t)[0] == "er" for t in tokens if t)
    optional, k, depth = set(), 0, 0
    for i, ch in enumerate(text):
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        elif "一" <= ch <= "鿿":
            if depth or (ch == "儿" and k > 0 and not er_syllable):
                optional.add(k)
            k += 1
    return optional


def near_syllable(a, b):
    """Syllables the recognizer confuses: an added or dropped final n (nǐ/nín, jǐ/jìn)
    and ia/iao (jiā/jiāo, xià/xiào)."""
    for x, y in ((a, b), (b, a)):
        if x + "n" == y or (x.endswith("ia") and x + "o" == y):
            return True
    return False


def unit_match(unit, heard, strict):
    """(matches, tone_ok) for one card unit vs one heard unit."""
    if heard[0] in unit[:2]:
        return True, True
    if heard[1] != unit[2]:
        # The recognizer mishears some syllables as near neighbours. Accept those; the
        # tone belongs to the misheard word, so it isn't judged.
        if near_syllable(heard[1], unit[2]):
            return True, True
        return False, False
    # 一 and 不 change tone with the next syllable (yí xià, bú shì) and the cards
    # write the changed tone, so don't hold their tone against you
    # Neutral tones can't be judged either: the recognizer reports characters, whose dictionary
    # tone is never neutral when the card's is (试试 shì shi), and vice versa (们, 的, 吗).
    tone_ok = heard[2] == unit[3] or unit[0] in SANDHI_CHARS or not unit[3] or not heard[2]
    return tone_ok or not strict, tone_ok


def find_within(units, heard, strict):
    """Look for the whole card as a contiguous run inside a longer utterance.
    Returns tone_ok of the best match, or None if not found."""
    n = len(units)
    best = None
    for i in range(len(heard) - n + 1):
        results = [unit_match(u, h, strict) for u, h in zip(units, heard[i:i + n])]
        if all(m for m, _ in results):
            tone_ok = all(t for _, t in results)
            if tone_ok:
                return True
            best = False
    return best


def align_units(units, heard, strict):
    """Align an utterance to the card's characters.

    Returns (matched, pairs): the card indices that were said correctly, and
    {card index: heard index} pairing every card character with the syllable
    heard in its place (matches, plus wrong syllables said in the gaps between them).
    """
    n, m = len(units), len(heard)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if unit_match(units[i], heard[j], strict)[0]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    anchors, i, j = [], 0, 0
    while i < n and j < m:
        if unit_match(units[i], heard[j], strict)[0] and dp[i][j] == dp[i + 1][j + 1] + 1:
            anchors.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    pairs = dict(anchors)
    # pair up substitutions in the gaps between matches; the leading gap is
    # aligned to its end so extra words before the answer don't shift it
    bounds = [(-1, -1)] + anchors + [(n, m)]
    for (ai, aj), (bi, bj) in zip(bounds, bounds[1:]):
        card_gap, heard_gap = list(range(ai + 1, bi)), list(range(aj + 1, bj))
        if ai == -1:
            card_gap, heard_gap = card_gap[::-1], heard_gap[::-1]
        pairs.update(zip(card_gap, heard_gap))
    return {i for i, _ in anchors}, pairs


def strip_hanzi(text):
    return "".join(ch for ch in text if "一" <= ch <= "鿿")


# ---------------------------------------------------------------- data

def deck_name(path):
    name = os.path.basename(path)
    name = re.sub(r"^yoyochinese_|_flashcards\.json$", "", name)
    return re.sub(r"\bIi\b", "II", name.replace("_", " ").title())


# Course order: conversational track first, then the character courses.
COURSE_ORDER = ["beginner", "intermediate_conv", "upper_intermediate", "advanced",
                "chinese_characters_flash", "chinese_characters_ii", "character_reader"]


def course_rank(path):
    name = os.path.basename(path)
    return next((i for i, key in enumerate(COURSE_ORDER) if key in name), len(COURSE_ORDER))


def load_decks():
    decks = {}
    for path in sorted(glob.glob(os.path.join(CARD_DIR, "*.json")), key=lambda p: (course_rank(p), p)):
        with open(path, encoding="utf-8") as f:
            decks[deck_name(path)] = json.load(f)
    return decks


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def input_devices():
    """Unique input devices on the MME host API (works with any sample rate)."""
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["hostapi"] == 0:
            out.append((i, d["name"]))
    return out


# ---------------------------------------------------------------- listener

class Listener(threading.Thread):
    """Feeds mic audio to a Mandarin recognizer, an English 'pass' spotter, and a
    Mandarin recognizer limited to the current card's characters.

    The free recognizer's language model sometimes swaps a correctly spoken
    syllable for a likelier word (你好 heard as 您好). The card-limited one has
    no such bias, so it is used to confirm near misses.
    """

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.audio = queue.Queue()
        self.cn_model = vosk.Model(CN_MODEL)
        self.en_model = vosk.Model(EN_MODEL)
        self.stream = None
        self.rate = 16000
        self.reset_flag = threading.Event()
        self.muted = False
        self.expected = ""  # hanzi of the current card
        self.cn = self.en = self.gr = None

    def close_device(self):
        """Release the mic entirely, so Bluetooth headsets can leave headset (call) mode."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def open_device(self, index):
        self.close_device()
        self.device = index
        info = sd.query_devices(index)
        self.rate = int(info["default_samplerate"])
        self._make_recognizers()
        self.stream = sd.RawInputStream(
            device=index, samplerate=self.rate, channels=1, dtype="int16",
            blocksize=int(self.rate * 0.05), callback=self._callback)
        self.stream.start()

    def _make_recognizers(self):
        cn = vosk.KaldiRecognizer(self.cn_model, self.rate)
        # Free-form English, not a ["pass"] grammar: a grammar forces Mandarin
        # syllables like 怕 (pà) onto "pass", free recognition hears them as "ha".
        en = vosk.KaldiRecognizer(self.en_model, self.rate)
        en.SetWords(True)
        gr = None
        if self.expected:
            grammar = json.dumps([" ".join(self.expected), "[unk]"], ensure_ascii=False)
            gr = vosk.KaldiRecognizer(self.cn_model, self.rate, grammar)
            gr.SetWords(True)
        for r in (cn, en, gr):
            try:  # shorter end-of-speech silence = snappier grading (vosk >= 0.3.45)
                if r:
                    r.SetEndpointerDelays(5.0, 0.35, 15.0)
            except AttributeError:
                pass
        self.cn, self.en, self.gr = cn, en, gr

    def _callback(self, indata, frames, t, status):
        self.audio.put(bytes(indata))

    def reset(self, expected=None):
        if expected is not None:
            self.expected = expected
        self.reset_flag.set()

    def run(self):
        en_final = gr_final = None
        while True:
            data = self.audio.get()
            if self.cn is None:
                continue
            if self.reset_flag.is_set():
                self.reset_flag.clear()
                while not self.audio.empty():  # drop audio from the previous card
                    self.audio.get_nowait()
                # Fresh recognizers per card: Reset() keeps speaker adaptation,
                # which drifts and made "pass" confidence decay over a session.
                self._make_recognizers()
                en_final = gr_final = None
                continue
            samples = np.frombuffer(data, dtype=np.int16)
            level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) else 0
            self.events.put(("level", level))
            if self.muted:
                continue

            cn, en, gr = self.cn, self.en, self.gr
            if gr and gr.AcceptWaveform(data):
                gr_final = json.loads(gr.Result())
            if en.AcceptWaveform(data):
                en_final = json.loads(en.Result())
                if self._is_pass(en_final):
                    self.cn.Reset()
                    self.events.put(("pass", None))
                    en_final = None
                    continue
            if cn.AcceptWaveform(data):
                text = json.loads(cn.Result()).get("text", "")
                if en_final is None:
                    en_final = json.loads(en.FinalResult())
                if gr_final is None and gr:
                    gr_final = json.loads(gr.FinalResult())
                card_words = [(w["word"], w.get("conf", 0)) for w in (gr_final or {}).get("result", [])]
                if self._is_pass(en_final):
                    self.events.put(("pass", None))
                elif text.strip():
                    self.events.put(("final", (text, card_words)))
                en_final = gr_final = None
            else:
                partial = json.loads(cn.PartialResult()).get("partial", "")
                if partial.strip():
                    self.events.put(("partial", partial))

    @staticmethod
    def _is_pass(result):
        words = (result or {}).get("result", [])
        return len(words) <= 2 and any(
            w.get("word") in PASS_WORDS and w.get("conf", 0) >= PASS_CONF for w in words)


class SelectableText(tk.Text):
    """A read-only, centered, word-wrapping label whose text can be selected and copied.
    Accepts the Label-style configure(text=..., fg=...) the app already uses."""

    def __init__(self, master, font, fg, bg):
        super().__init__(master, font=font, fg=fg, bg=bg, bd=0, highlightthickness=0, height=1,
                         wrap="word", cursor="xterm", padx=0, pady=0, takefocus=0,
                         selectbackground=VIOLET, selectforeground="white", inactiveselectbackground=VIOLET)
        self.tag_configure("center", justify="center")
        self.configure(state="disabled")
        self.bind("<Button-1>", lambda e: self.focus_set(), add="+")
        self.bind("<Control-c>", lambda e: self.event_generate("<<Copy>>"))

    def configure(self, cnf=None, **kw):
        text = kw.pop("text", None)
        kw.pop("wraplength", None)
        if kw or cnf:
            super().configure(cnf, **kw)
        if text is not None:
            super().configure(state="normal")
            self.delete("1.0", "end")
            self.insert("1.0", text, "center")
            super().configure(state="disabled")
            self.refit()

    config = configure

    def get_text(self):
        return self.get("1.0", "end-1c")

    def selected(self):
        try:
            return self.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def refit(self):
        """Grow or shrink to the wrapped text, like a Label."""
        if not self.get_text():
            super().configure(height=1)
            return
        self.update_idletasks()
        lines = self.count("1.0", "end-1c", "displaylines")
        lines = (lines[0] if isinstance(lines, tuple) else lines) or 0
        super().configure(height=lines + 1)


class Speaker(threading.Thread):
    """Reads cards aloud with the Windows Chinese voice (Microsoft Huihui)."""

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.texts = queue.Queue()
        self.available = True

    def say(self, text):
        self.texts.put(text)

    def run(self):
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            # dynamic=True: no generated wrapper modules, which a built exe can't write
            voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
            voices = voice.GetVoices()
            for i in range(voices.Count):
                desc = voices.Item(i).GetDescription()
                if "Chinese" in desc or "Huihui" in desc:
                    voice.Voice = voices.Item(i)
                    break
            else:
                raise RuntimeError("no Chinese voice installed")
            voice.Rate = TTS_RATE
            voice.Volume = TTS_VOLUME
        except Exception:
            self.available = False
            return
        while True:
            text = self.texts.get()
            while not self.texts.empty():  # several presses: only say the latest
                text = self.texts.get_nowait()
            self.events.put(("speaking", True))
            try:
                voice.Speak(text, 0)
            finally:
                self.events.put(("speaking", False))


class Hotkey(threading.Thread):
    """System-wide shortcuts that work while a game has focus:
    Ctrl+Alt+M switches the mic on/off, Ctrl+Alt+H says the card aloud,
    Ctrl+Alt+G / N / F grade the card Good / Not Bad / Forgot."""

    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x0312
    KEYS = {1: "M", 2: "H", 3: "G", 4: "N", 5: "F"}

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.registered = None

    def run(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        mods = self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT
        # a combination another program already owns just doesn't register; the buttons still work
        self.registered = {k: bool(user32.RegisterHotKey(None, k, mods, ord(key))) for k, key in self.KEYS.items()}
        if not any(self.registered.values()):
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                self.events.put(("hotkey", msg.wParam))


# ---------------------------------------------------------------- app

class App:
    def __init__(self, root):
        self.root = root
        self.decks = load_decks()
        self.all_cards = {c["code"]: c for cards in self.decks.values() for c in cards}
        self.progress = load_json(PROGRESS_FILE, {})
        for code, c in self.all_cards.items():
            if c.get("mastered") and code not in self.progress:
                self.progress[code] = {"level": MASTERED, "ok": 0, "fail": 0}
        for p in self.progress.values():  # older progress files stored mastered yes/no
            if "level" not in p:
                attempted = p.get("ok", 0) or p.get("fail", 0)
                p["level"] = MASTERED if p.get("mastered") else 0 if attempted else -1
            p.pop("mastered", None)
        self.settings = load_json(SETTINGS_FILE, {})

        self.events = queue.Queue()
        self.queue = []
        self.card = None
        self.locked = False      # True while showing a result before advancing
        self.must_repeat = False
        self.passed_at = 0.0
        self.asleep = False                # mic released to save headphone battery
        self.last_heard = time.time()      # last time the recognizer heard speech
        self.due = None                    # codes YoYo says are due (None: not fetched / offline)
        self.due_time = 0.0
        self.round_requested = False       # waiting for YoYo's next due list
        self.speaking = False              # the app is reading the card aloud
        self.deaf_until = 0.0              # ignore the mic until then (echo of the app's voice)
        self.advance_job = None
        self.anim_jobs = {}
        self.streak = self.right = self.passed = 0
        self.level = 0.0

        self._build_ui()
        self.listener = Listener(self.events)
        self.listener.start()
        self.sync = YoyoSync(self.events, YOYO_AUTH_FILE, YOYO_CACHE_FILE, YOYO_LOG_FILE)
        self.sync.start()
        self._select_mic(initial=True)
        if self.sync.signed_in:  # wait for YoYo's due list (falls back if it doesn't come)
            self.root.after(DUE_WAIT_S * 1000, self._due_fallback)
        else:
            self._rebuild_queue()
        self._next_card()
        self.root.after(30, self._poll)
        self.root.after(5000, self._sleep_tick)
        self._log_day()  # today's point on the progress chart, even before any answer
        self.hotkey = Hotkey(self.events)
        self.hotkey.start()
        self.speaker = Speaker(self.events)
        self.speaker.start()
        for w in self.card_widgets:  # clicking the card turns a sleeping mic back on
            w.bind("<Button-1>", lambda e: self._wake(), add="+")
            w.bind("<Button-3>", self._card_menu)  # right-click: copy menu

    # ---------- UI

    def _build_ui(self):
        r = self.root
        r.title("Sidebar Study")
        try:
            r.iconbitmap(os.path.join(RES, "icon.ico"))
        except tk.TclError:
            pass
        r.configure(bg=BG)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 1.0)
        r.geometry(self.settings.get("geometry", "400x360+40+40"))
        r.minsize(300, 170)
        r.protocol("WM_DELETE_WINDOW", self._quit)

        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=BTN, background=BTN, foreground=INK,
                        arrowcolor=VIOLET, bordercolor=BTN, lightcolor=BTN, darkcolor=BTN,
                        padding=3)
        style.map("TCombobox", fieldbackground=[("readonly", BTN)], foreground=[("readonly", INK)],
                  background=[("readonly", BTN)], selectbackground=[("readonly", BTN)],
                  selectforeground=[("readonly", INK)])
        r.option_add("*TCombobox*Listbox.background", BTN)
        r.option_add("*TCombobox*Listbox.foreground", INK)
        r.option_add("*TCombobox*Listbox.selectBackground", VIOLET)
        r.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

        # header: deck picker, session score and streak on a violet bar
        top = tk.Frame(r, bg=PANEL)
        top.pack(fill="x")
        names = ["All decks"] + list(self.decks)
        self.deck_var = tk.StringVar(value=self.settings.get("deck", names[0]))
        if self.deck_var.get() not in names:
            self.deck_var.set(names[0])
        deck_box = ttk.Combobox(top, textvariable=self.deck_var, values=names,
                                state="readonly", width=22, font=("Segoe UI", 10, "bold"))
        deck_box.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=8)
        deck_box.bind("<<ComboboxSelected>>", lambda e: self._on_deck())
        self.streak_lbl = tk.Label(top, bg=PANEL, fg=YELLOW, font=("Segoe UI", 12, "bold"))
        self.streak_lbl.pack(side="right", padx=(4, 10))
        self.score_lbl = tk.Label(top, bg=PANEL, fg=INK, font=("Segoe UI", 11, "bold"))
        self.score_lbl.pack(side="right", padx=4)
        self.sync_lbl = tk.Label(top, bg=PANEL, fg=DIM, font=("Segoe UI", 9, "bold"), cursor="hand2")
        self.sync_lbl.pack(side="right", padx=(4, 8))
        self.sync_lbl.bind("<Button-1>", lambda e: self._sync_clicked())
        self.header_stats = tk.Label(top, bg=PANEL, fg=DIM, font=("Segoe UI", 9, "bold"))

        # deck progress bar + count
        prog = tk.Frame(r, bg=BG)
        prog.pack(fill="x", padx=12, pady=(8, 0))
        self.stats = tk.Label(prog, bg=BG, fg=DIM, font=("Segoe UI", 10, "bold"))
        self.stats.pack(side="right", padx=(8, 0))
        self.progress_bar = tk.Canvas(prog, height=8, bg=TRACK, highlightthickness=0)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        self.progress_fill = self.progress_bar.create_rectangle(0, 0, 0, 8, fill=PINK, width=0)
        self.progress_bar.bind("<Configure>", lambda e: self._update_stats())

        # the card: a white panel whose border glows while you speak
        self.card_frame = tk.Frame(r, bg=CARD, highlightthickness=3,
                                   highlightbackground=BORDER, highlightcolor=BORDER)
        self.card_frame.pack(fill="both", expand=True, padx=12, pady=10)
        card = self.card_frame
        # Three blocks, arranged by _apply_layout: stacked when the window is tall,
        # side by side (characters left, the rest right) when it's short and wide.
        card_top = tk.Frame(card, bg=CARD)      # Hear it, ★ Master, YoYo level
        self.card_main = tk.Frame(card, bg=CARD)  # characters + traditional
        self.card_side = tk.Frame(card, bg=CARD)  # meaning + status
        self.card_top = card_top
        self.layout_mode = None
        self.side_empty = False  # nothing to show beside the characters (see _fit_side)
        self.speak_btn = self._button(card_top, "🔊 Hear it", self._speak, TEAL)
        self.speak_btn.pack(side="left")
        master = self._button(card_top, "★ Master", self._master, YELLOW)
        master.configure(font=("Segoe UI", 9, "bold"), padx=6, pady=2)
        master.pack(side="left", padx=(6, 0))
        self.level_bar = tk.Canvas(card_top, width=120, height=14, bg=CARD, highlightthickness=0)
        self.level_bar.pack(side="right")
        # A canvas so each character can be colored and carry its pinyin on top.
        self.hanzi = tk.Canvas(self.card_main, bg=CARD, height=80, highlightthickness=0)
        self.hanzi.pack(fill="x", padx=8, pady=(2, 0))
        self.hanzi.bind("<Configure>", self._on_hanzi_resize)
        self.hz_args, self.hz_dx, self.hz_width, self.fonts = {}, 0, 0, {}
        self.hz_hits, self.hz_sel, self.hz_anchor = [], None, None
        self.hz_layout = None  # (key, size, chars per line, height) of the last full layout
        self.hanzi.configure(cursor="xterm")
        self.hanzi.bind("<ButtonPress-1>", self._hz_press, add="+")
        self.hanzi.bind("<B1-Motion>", self._hz_drag)
        self.hanzi.bind("<Double-1>", self._hz_select_all)
        self.hanzi.bind("<Control-c>", self._copy_selection)
        # traditional characters aren't shown (the right-click menu can still copy them)
        self.trad = SelectableText(self.card_main, font=(HANZI_FONT, 14, "bold"), fg=DIM, bg=CARD)
        self.english = SelectableText(self.card_side, font=("Segoe UI", 14, "bold"), fg=INK, bg=CARD)
        self.english.pack(fill="x", padx=12, pady=(2, 0))
        self.status = SelectableText(self.card_side, font=("Segoe UI", 12, "bold"), fg=DIM, bg=CARD)
        self.status.pack(fill="x", padx=12, pady=(8, 10))
        self.card_widgets = [card, card_top, self.card_main, self.card_side, self.level_bar,
                             self.hanzi, self.trad, self.english, self.status]
        card.bind("<Configure>", self._on_resize)
        r.bind("<Configure>", self._on_window_resize, add="+")

        bottom = tk.Frame(r, bg=BG)
        # packed before the card so a short window squeezes the card, not the buttons
        bottom.pack(fill="x", padx=12, pady=(0, 10), side="bottom", before=self.card_frame)
        self.header, self.prog_row, self.bottom = top, prog, bottom
        self.layout_mode = ("tall", False)
        self._apply_layout("tall", False)  # until the window reports its real size
        self.mic_icon = tk.Label(bottom, text="🎙", bg=BG, fg=TEAL, font=("Segoe UI Emoji", 12))
        self.mic_icon.pack(side="left", padx=(0, 4))
        self.meter = tk.Canvas(bottom, width=56, height=12, bg=TRACK, highlightthickness=0)
        self.meter.pack(side="left")
        self.meter_bar = self.meter.create_rectangle(0, 0, 0, 12, fill=TEAL, width=0)
        self.mute_btn = self._button(bottom, "Mic off", self._toggle_mute, BLUE)
        self.mute_btn.pack(side="left", padx=(8, 0))
        self._button(bottom, "⚙", self._toggle_options, VIOLET).pack(side="right")
        grades = tk.Frame(r, bg=BG)  # YoYo's grade buttons, full width under the card
        grades.pack(fill="x", padx=12, pady=(0, 6), side="bottom", before=self.card_frame)
        for i, (answer, text, color) in enumerate((("hard", "Forgot", PINK), ("good", "Not Bad", ORANGE),
                                                   ("easy", "Good", GREEN))):
            b = self._button(grades, text, lambda a=answer: self._grade(a), color)
            b.configure(pady=6, font=("Segoe UI", 11, "bold"))
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            grades.columnconfigure(i, weight=1, uniform="grade")
        self.grades = grades
        self.mastery_btn = self._button(bottom, "Mastery", self._open_mastery, TEAL)
        self.mastery_btn.pack(side="right", padx=(0, 6))

        opt_bg = PANEL
        self.options = tk.Frame(r, bg=opt_bg)
        devices = input_devices()
        self.mic_map = {name: idx for idx, name in devices}
        self.mic_var = tk.StringVar(value=self.settings.get("mic", ""))
        if self.mic_var.get() not in self.mic_map:
            self.mic_var.set(devices[0][1] if devices else "")
        tk.Label(self.options, text="Mic", bg=opt_bg, fg=INK,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        mic_box = ttk.Combobox(self.options, textvariable=self.mic_var,
                               values=list(self.mic_map), state="readonly", width=30)
        mic_box.grid(row=0, column=1, columnspan=2, sticky="we", padx=8, pady=6)
        mic_box.bind("<<ComboboxSelected>>", lambda e: self._select_mic())
        self.review_var = tk.BooleanVar(value=self.settings.get("include_mastered", False))
        self.english_var = tk.BooleanVar(value=self.settings.get("show_english", False))
        for col, (text, var, cmd) in enumerate([
                ("Review mastered", self.review_var, self._on_deck),
                ("Show English", self.english_var, self._show_card)]):
            tk.Checkbutton(self.options, text=text, variable=var, command=cmd, bg=opt_bg,
                           fg=INK, selectcolor=BTN, activebackground=opt_bg,
                           activeforeground=VIOLET, font=("Segoe UI", 9, "bold")).grid(
                row=1, column=col, sticky="w", padx=6)
        tk.Label(self.options, text="Next card after", bg=opt_bg, fg=INK,
                 font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w", padx=8)
        self.delay_var = tk.DoubleVar(value=self.settings.get("advance_s", 3.0))
        stepper = tk.Frame(self.options, bg=opt_bg)
        stepper.grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        self._button(stepper, "−", lambda: self._change_delay(-0.5), VIOLET).pack(side="left")
        self.delay_lbl = tk.Label(stepper, bg=opt_bg, fg=INK, width=6, font=("Segoe UI", 11, "bold"),
                                  text=f"{self.delay_var.get():g} s")
        self.delay_lbl.pack(side="left")
        self._button(stepper, "+", lambda: self._change_delay(0.5), VIOLET).pack(side="left")
        tk.Label(self.options, text="Mic sleeps after", bg=opt_bg, fg=INK,
                 font=("Segoe UI", 9, "bold")).grid(row=3, column=0, sticky="w", padx=8)
        self.sleep_var = tk.IntVar(value=self.settings.get("sleep_min", 2))
        stepper = tk.Frame(self.options, bg=opt_bg)
        stepper.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        self._button(stepper, "−", lambda: self._change_sleep(-1), VIOLET).pack(side="left")
        self.sleep_lbl = tk.Label(stepper, bg=opt_bg, fg=INK, width=6, font=("Segoe UI", 11, "bold"),
                                  text=self._sleep_text())
        self.sleep_lbl.pack(side="left")
        self._button(stepper, "+", lambda: self._change_sleep(1), VIOLET).pack(side="left")
        tk.Label(stepper, text=f"of silence\n{HOTKEY_NAME}: mic on/off · {SPEAK_HOTKEY_NAME}: hear it"
                               f"\n{' / '.join(GRADE_HOTKEY_NAMES.values())}: Good / Not Bad / Forgot",
                 bg=opt_bg, fg=DIM, justify="left", font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))
        tk.Label(self.options, text="YoYo sync", bg=opt_bg, fg=INK,
                 font=("Segoe UI", 9, "bold")).grid(row=4, column=0, sticky="w", padx=8)
        yoyo = tk.Frame(self.options, bg=opt_bg)
        yoyo.grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=(4, 8))
        self._button(yoyo, "Sign in…", self._yoyo_sign_in, TEAL).pack(side="left")
        self._button(yoyo, "Sync now", self._sync_clicked, BLUE).pack(
            side="left", padx=(6, 0))
        self.options.columnconfigure(1, weight=1)

    def _button(self, parent, text, cmd, color):
        b = tk.Button(parent, text=text, command=cmd, bg=BTN, fg=color, bd=0,
                      activebackground=blend(BTN, color, 0.35), activeforeground="white",
                      padx=9, pady=4, font=("Segoe UI", 10, "bold"), cursor="hand2")
        b.base_color = color
        b.bind("<Enter>", lambda e: b.configure(bg=blend(BTN, b.base_color, 0.2)))
        b.bind("<Leave>", lambda e: b.configure(bg=BTN))
        return b

    def _on_resize(self, event):
        self.root.after_idle(lambda: [t.refit() for t in (self.trad, self.english, self.status)])
        if event.height != getattr(self, "card_h", None):  # more or less room for the characters
            self.card_h = event.height
            self.root.after_idle(self._draw_hanzi)


    def _on_window_resize(self, event):
        if event.widget is not self.root:
            return
        w, h = event.width, event.height
        self.mastery_btn.configure(text="📊" if w < 440 else "Mastery")  # narrow: icon only
        mode = "wide" if w >= h * WIDE_RATIO else "tall"
        compact = h < COMPACT_BELOW
        if (mode, compact) != self.layout_mode:
            self.layout_mode = (mode, compact)
            self._apply_layout(mode, compact)

    def _apply_layout(self, mode, compact):
        """Tall: blocks stacked. Wide: characters on the left, everything else on the right.
        Compact (short window): tighter spacing and smaller text."""
        card = self.card_frame
        for b in (self.card_top, self.card_main, self.card_side):
            b.grid_forget()
        for i in range(3):
            card.grid_rowconfigure(i, weight=0)
            card.grid_columnconfigure(i, weight=0, uniform="")
        pad = 4 if compact else 8
        if mode == "wide":
            card.grid_columnconfigure(0, weight=3, uniform="cols")
            card.grid_columnconfigure(1, weight=2, uniform="cols")
            card.grid_rowconfigure(1, weight=1)
            # no n/s stickiness: the character block sits centered vertically on the left
            if self.side_empty:  # nothing beside the characters: they get the whole width
                self.card_top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(pad, 0))
                self.card_main.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, pad))
            else:
                self.card_main.grid(row=0, column=0, rowspan=2, sticky="ew", pady=pad)
                self.card_top.grid(row=0, column=1, sticky="new", padx=10, pady=(pad, 0))
                self.card_side.grid(row=1, column=1, sticky="new", pady=(pad, 0))
        else:
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
            card.grid_rowconfigure(2, weight=1)
            # characters pinned to the bottom of their half and the meaning to the top of
            # its half, so they meet in the middle of the card with no gap between them
            self.card_top.grid(row=0, column=0, sticky="ew", padx=10, pady=(pad, 0))
            self.card_main.grid(row=1, column=0, sticky="sew")
            self.card_side.grid(row=2, column=0, sticky="new")
        self.level_bar.configure(width=76 if mode == "wide" else 120)
        if self.card:
            self._draw_level_bar(self._level(self.card["code"]))
        small = compact or mode == "wide"
        self.english.configure(font=("Segoe UI", 12 if small else 14, "bold"))
        self.status.configure(font=("Segoe UI", 11 if small else 12, "bold"))
        self.status.pack_configure(pady=(4, pad) if small else (8, 10))
        if compact:  # no room for a progress row: the count moves into the header
            self.prog_row.pack_forget()
            self.header_stats.pack(side="right", padx=4, before=self.score_lbl)
        else:
            self.header_stats.pack_forget()
            self.prog_row.pack(fill="x", padx=12, pady=(8, 0), after=self.header)
        self.card_frame.pack_configure(pady=6 if compact else 10)
        self.bottom.pack_configure(pady=(0, 6 if compact else 10))
        self.root.after_idle(self._draw_hanzi)
        self.root.after_idle(lambda: [t.refit() for t in (self.trad, self.english, self.status)])

    def _hanzi_room(self):
        """Vertical pixels the characters (with their pinyin) may use."""
        card_h = self.card_frame.winfo_height()
        if card_h < 50:
            return 10_000  # not laid out yet
        used = 16
        if self.layout_mode and self.layout_mode[0] == "tall":
            used += self.card_top.winfo_reqheight() + self.card_side.winfo_reqheight()
        return max(40, card_h - used)

    def _render_hanzi(self, matched=None, final=False, text=None, color=INK, ruby=None,
                      ruby_color=None, tone_off=None, reveal=False, said=None):
        """Draw the card's characters with pinyin above them.

        matched: card character indices said correctly (green; the rest red if final).
        ruby: {card character index: pinyin} shown above each character.
        tone_off: indices with the right sounds but the wrong tone (yellow).
        reveal: the pinyin is the answer, so draw it bigger than the characters.
        said: {index: (pinyin you said, "ok"|"tone"|"bad"|"skip")}: drawn under each character,
              so the correct pronunciation (🎯 top) and yours (🎙 bottom) line up column by column.
        """
        if reveal:  # remembered so retries can redraw the answer with your latest attempt under it
            self.reveal_args = dict(matched=matched, ruby=ruby, ruby_color=ruby_color, tone_off=tone_off)
        self.hz_args = dict(text=self.card["simplified"] if text is None else text,
                            matched=matched or set(), final=final, color=color,
                            ruby=ruby or {}, ruby_color=ruby_color, bump=0,
                            tone_off=tone_off or set(), reveal=reveal,
                            said=said if reveal else None)
        self.hz_dx = 0
        self.hz_sel = None
        self._draw_hanzi()

    def _font(self, family, size):
        key = (family, size)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family=family, size=size, weight="bold")
        return self.fonts[key]

    def _draw_hanzi(self):
        a = self.hz_args
        if not a:
            return
        c, text = self.hanzi, a["text"]
        width = c.winfo_width() if c.winfo_width() > 20 else 380
        compare = a["said"] is not None  # two pinyin rows: 🎯 correct (top), 🎙 you said (bottom)
        gutter = COMPARE_GUTTER if compare else 0
        avail = width - 16 - gutter
        if a["bump"] and self.hz_layout and self.hz_layout[0] == (id(a), width):
            # a pop frame: reuse the normal-size layout instead of re-fitting (keeps it smooth)
            _, size, line_lengths, base_h = self.hz_layout
            return self._paint_hanzi(a, size, line_lengths, base_h, width, gutter, compare)
        room = self._hanzi_room()
        # start big so the characters fill the card, then shrink to fit on one line, but only
        # down to a readable size for its length: below that a long sentence wraps instead
        floor = 46 if len(text) <= 4 else 36 if len(text) <= 8 else 28 if len(text) <= 14 else 22
        size = HANZI_MAX
        while size > floor and sum(cell[2] for cell in self._hanzi_cells(text, size)[0]) > avail:
            size -= 4 if size > 60 else 2

        def heights(rb, hz, sd):
            return rb.metrics("linespace"), hz.metrics("linespace"), sd.metrics("linespace") if compare else 0

        def wrap(size):
            cells, hz, rb, sd = self._hanzi_cells(text, size)
            lines, line, line_w = [], [], 0
            for cell in cells:  # wrap to the canvas width
                if line and line_w + cell[2] > avail:
                    lines.append((line, line_w))
                    line, line_w = [], 0
                line.append(cell)
                line_w += cell[2]
            lines.append((line, line_w))
            rb_h, hz_h, sd_h = heights(rb, hz, sd)
            return lines, (hz, rb, sd), (rb_h, hz_h, sd_h), len(lines) * (rb_h + hz_h + sd_h) + 2 * POP_MARGIN

        # and shrink until it fits the height available (short windows)
        lines, fonts, hs, needed = wrap(size)
        while needed > room and size > 14:
            size -= 4 if size > 60 else 2
            lines, fonts, hs, needed = wrap(size)
        base_h = len(lines) * sum(hs)
        line_lengths = [len(line) for line, _ in lines]
        self.hz_layout = ((id(a), width), size, line_lengths, base_h)
        self._paint_hanzi(a, size, line_lengths, base_h, width, gutter, compare)

    def _paint_hanzi(self, a, size, line_lengths, base_h, width, gutter, compare):
        """Draw the cells at `size` (+ the pop's bump) on the given line breaks. During the
        success "pop" the glyphs are bigger but the line breaks stay the same and the block is
        centered on the normal one, spilling past the margins rather than re-wrapping."""
        c = self.hanzi
        cells, hz, rb, sd = self._hanzi_cells(a["text"], size + a["bump"])
        rb_h, hz_h = rb.metrics("linespace"), hz.metrics("linespace")
        sd_h = sd.metrics("linespace") if compare else 0
        it, lines = iter(cells), []
        for n in line_lengths:
            line = [next(it) for _ in range(n)]
            lines.append((line, sum(cell[2] for cell in line)))
        y = POP_MARGIN + (base_h - len(lines) * (rb_h + hz_h + sd_h)) / 2

        c.delete("all")
        self.hz_hits = []  # (x0, x1, y0, y1, char) per drawn character, for mouse selection
        icon = self._font("Segoe UI Emoji", max(11, round(sd.cget("size") * 0.9)))
        for line, line_w in lines:
            x = gutter + (width - gutter - line_w) / 2 + self.hz_dx
            if compare:  # row labels in the left margin
                c.create_text(gutter / 2, y + rb_h / 2, text="🎯", font=icon, fill=INK)
                c.create_text(gutter / 2, y + rb_h + hz_h + sd_h / 2, text="🎙", font=icon, fill=INK)
            for ch, syl, w, fill, rfill, said, sfill in line:
                k = len(self.hz_hits)
                if self.hz_sel and self.hz_sel[0] <= k <= self.hz_sel[1]:
                    c.create_rectangle(x, y + rb_h, x + w, y + rb_h + hz_h, fill=VIOLET, width=0)
                if syl:
                    c.create_text(x + w / 2, y, text=syl, font=rb, fill=rfill, anchor="n")
                c.create_text(x + w / 2, y + rb_h, text=ch, font=hz, fill=fill, anchor="n")
                if said:
                    c.create_text(x + w / 2, y + rb_h + hz_h, text=said, font=sd, fill=sfill, anchor="n")
                self.hz_hits.append((x, x + w, y, y + rb_h + hz_h, ch))
                x += w
            y += rb_h + hz_h + sd_h
        c.configure(height=base_h + 2 * POP_MARGIN)  # same size during the pop: nothing shifts

    # ---------- selecting and copying card text

    def _hz_index(self, x, y):
        """The character under (or nearest to) the mouse."""
        best, dist = None, None
        for k, (x0, x1, y0, y1, _) in enumerate(self.hz_hits):
            dx = 0 if x0 <= x < x1 else min(abs(x - x0), abs(x - x1))
            dy = 0 if y0 <= y < y1 else min(abs(y - y0), abs(y - y1))
            d = dy * 1000 + dx  # prefer the right line, then the nearest character on it
            if dist is None or d < dist:
                best, dist = k, d
        return best

    def _hz_press(self, e):
        self.hanzi.focus_set()
        self.hz_anchor = self._hz_index(e.x, e.y)
        if self.hz_sel:
            self.hz_sel = None
            self._draw_hanzi()

    def _hz_drag(self, e):
        k = self._hz_index(e.x, e.y)
        if self.hz_anchor is None or k is None:
            return
        sel = (min(self.hz_anchor, k), max(self.hz_anchor, k))
        if sel != self.hz_sel:
            self.hz_sel = sel
            self._draw_hanzi()

    def _hz_select_all(self, e=None):
        if self.hz_hits:
            self.hz_sel = (0, len(self.hz_hits) - 1)
            self._draw_hanzi()

    def _hz_selected(self):
        if not self.hz_sel:
            return ""
        return "".join(h[4] for h in self.hz_hits[self.hz_sel[0]:self.hz_sel[1] + 1])

    def _copy(self, text):
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _copy_selection(self, e=None):
        focused = self.root.focus_get()
        if isinstance(focused, SelectableText) and focused.selected():
            self._copy(focused.selected())
        else:
            self._copy(self._hz_selected())
        return "break"

    def _card_menu(self, e):
        c = self.card
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=INK, activebackground=VIOLET,
                    activeforeground="white", bd=0, font=("Segoe UI", 10))
        focused = self.root.focus_get()
        has_sel = bool(self._hz_selected() or (isinstance(focused, SelectableText) and focused.selected()))
        m.add_command(label="Copy selection", command=self._copy_selection,
                      state="normal" if has_sel else "disabled")
        if c:
            m.add_separator()
            m.add_command(label=f"Copy characters   {c['simplified']}", command=lambda: self._copy(c["simplified"]))
            if c["traditional"] != c["simplified"]:
                m.add_command(label="Copy traditional", command=lambda: self._copy(c["traditional"]))
            m.add_command(label="Copy pinyin", command=lambda: self._copy(c["pinyin"]))
            m.add_command(label="Copy English", command=lambda: self._copy(c["english"]))
            m.add_command(label="Copy all", command=lambda: self._copy(
                f"{c['simplified']}\n{c['pinyin']}\n{c['english']}"))
        m.tk_popup(e.x_root, e.y_root)

    def _hanzi_cells(self, text, size):
        """Lay out (char, pinyin, width, color, pinyin color, said, said color) cells at a font
        size. Returns cells and the fonts for characters, pinyin (top) and what you said (bottom)."""
        a = self.hz_args
        if a["reveal"]:
            hz = self._font(HANZI_FONT, round(size * 0.7))
            rb = self._font("Segoe UI", size)
        else:
            hz = self._font(HANZI_FONT, size)
            rb = self._font("Segoe UI", max(13, round(size * 0.4)))
        sd = self._font("Segoe UI", max(12, round(size * 0.45)))
        said_colors = {"ok": GREEN, "tone": YELLOW, "bad": RED, "skip": DIM}
        cells, k = [], 0  # k: index among hanzi characters, as in card_units
        for ch in text:
            is_hz = "\u4e00" <= ch <= "\u9fff"
            syl = a["ruby"].get(k) if is_hz else None
            said, sfill = None, DIM
            if is_hz:
                fill = GREEN if k in a["matched"] else RED if a["final"] else a["color"]
                rfill = a["ruby_color"] or (GREEN if k in a["matched"] else RED if a["final"] else DIM)
                if k in a["tone_off"]:  # with the 🎙 row, the slip shows there; 🎯 stays the answer's color
                    fill = YELLOW
                    if a["said"] is None:
                        rfill = YELLOW
                if a["said"] is not None:
                    said, state = a["said"].get(k, ("\u2014", "bad"))
                    said, sfill = ("\u2713" if state == "ok" else said), said_colors[state]
                k += 1
            else:
                fill, rfill = a["color"], DIM
            w = max(hz.measure(ch), rb.measure(syl) if syl else 0, sd.measure(said) if said else 0) \
                + (10 if syl or said else 2)
            cells.append((ch, syl, w, fill, rfill, said, sfill))

        return cells, hz, rb, sd

    def _on_hanzi_resize(self, event):
        if event.width != self.hz_width:  # ignore height changes we cause ourselves
            self.hz_width = event.width
            self._draw_hanzi()

    def _change_delay(self, step):
        self.delay_var.set(min(10.0, max(1.0, self.delay_var.get() + step)))
        self.delay_lbl.configure(text=f"{self.delay_var.get():g} s")
        self._save_settings()

    def _toggle_options(self):
        if self.options.winfo_ismapped():
            self.options.pack_forget()
        else:
            self.options.pack(fill="x", padx=12, pady=(0, 8), side="bottom", before=self.card_frame)

    # ---------- animation

    def _animate(self, name, frames, step, ms=30):
        """Run step(i) for i in range(frames), replacing any running animation of that name."""
        job = self.anim_jobs.pop(name, None)
        if job:
            self.root.after_cancel(job)

        def tick(i=0):
            step(i)
            if i + 1 < frames:
                self.anim_jobs[name] = self.root.after(ms, tick, i + 1)
            else:
                self.anim_jobs.pop(name, None)
        tick()

    def _set_card_bg(self, color):
        for w in self.card_widgets:
            w.configure(bg=color)

    def _flash(self, color):
        """Tint the card with color, then fade back to white."""
        frames = 16
        start = blend(CARD, color, 0.45)
        self._animate("flash", frames, lambda i: self._set_card_bg(blend(start, CARD, i / (frames - 1))))

    def _pop(self):
        """Briefly enlarge the characters."""
        bumps = [3, 6, 8, 6, 4, 2, 0]

        def step(i):
            self.hz_args["bump"] = bumps[i]
            self._draw_hanzi()
        self._animate("pop", len(bumps), step, ms=35)

    def _shake(self):
        offsets = [14, -14, 10, -10, 6, -6, 2, 0]
        def step(i):
            self.hz_dx = offsets[i]
            self._draw_hanzi()
        self._animate("shake", len(offsets), step, ms=35)

    def _pulse_label(self, label, color, base):
        frames = 14
        self._animate(f"pulse{id(label)}", frames,
                      lambda i: label.configure(fg=blend(color, base, i / (frames - 1))), ms=40)

    # ---------- deck / queue

    def _deck_cards(self):
        name = self.deck_var.get()
        if name in self.decks:
            return self.decks[name]
        return [c for cards in self.decks.values() for c in cards]

    def _level(self, code):
        return self.progress.get(code, {}).get("level", -1)

    def _is_mastered(self, code):
        return self._level(code) >= MASTERED

    def _in_progress(self, code):
        p = self.progress.get(code, {})
        started = p.get("level", -1) >= 0 or p.get("ok", 0) or p.get("fail", 0)
        return bool(started) and p.get("level", -1) < MASTERED

    def _rebuild_queue(self):
        """With YoYo: the reviews YoYo says are due (its spaced-repetition schedule), then a
        batch of new cards from your current level in lesson order. Without YoYo (offline or
        signed out): cards you've started, lowest level first, then new cards in course order."""
        cards = self._deck_cards()
        current = self.card["code"] if self.card else None

        def shuffled_groups(items, key):
            groups = {}
            for c in items:  # dicts keep insertion order, so group order follows `items`
                groups.setdefault(key(c), []).append(c)
            out = []
            for k in (sorted(groups) if key is not lesson_of else groups):
                random.shuffle(groups[k])
                out += groups[k]
            return out

        def lesson_of(c):
            return c["code"].rsplit("-", 1)[0]  # ICC-011-01-004 -> ICC-011-01

        new = [c for c in cards if not self._is_mastered(c["code"]) and not self._in_progress(c["code"])]
        if self.due is not None:
            new = [c for c in cards if self._level(c["code"]) < 0]  # new on YoYo, whatever was done here
            in_deck = {c["code"]: c for c in cards}
            answered = {k for k, p in self.progress.items() if p.get("last", 0) > self.due_time}
            due = [in_deck[k] for k in dict.fromkeys(self.due) if k in in_deck and k not in answered]
            # new cards only from the first level that still has some: keeps you on one unit
            level_of = lambda c: (self.sync.cache["cards"].get(c["code"], {}).get("levelId")
                                  or (c.get("level"), c["code"].split("-")[0]))
            focus = level_of(new[0]) if new else None
            batch = shuffled_groups([c for c in new if level_of(c) == focus], lesson_of)[:NEW_PER_ROUND]
            self.queue = due + batch
        else:
            started = [c for c in cards if self._in_progress(c["code"])]
            self.queue = (shuffled_groups(started, lambda c: max(self._level(c["code"]), 0))
                          + shuffled_groups(new, lesson_of))
        if self.review_var.get():
            mastered = [c for c in cards if self._is_mastered(c["code"])]
            random.shuffle(mastered)
            self.queue += mastered
        self.queue = [c for c in self.queue if c["code"] != current]

    def _on_deck(self):
        self._save_settings()
        self._rebuild_queue()
        self._next_card()

    def _update_stats(self):
        cards = self._deck_cards()
        done = sum(self._is_mastered(c["code"]) for c in cards)
        self.stats.configure(text=f"{done:,} / {len(cards):,}")
        self.header_stats.configure(text=f"{done:,}/{len(cards):,}")
        w = self.progress_bar.winfo_width()
        self.progress_bar.coords(self.progress_fill, 0, 0, w * done / max(1, len(cards)), 8)
        self.score_lbl.configure(text=f"✓ {self.right}   ✗ {self.passed}")
        self.streak_lbl.configure(text=f"🔥 {self.streak}" if self.streak else "")

    # ---------- card flow

    def _next_card(self):
        if self.advance_job:
            self.root.after_cancel(self.advance_job)
            self.advance_job = None
        self.locked = False
        self.solved = False      # meaning is hidden until the card is answered or passed
        self.must_repeat = False  # after "pass", the card stays until said correctly (no credit)
        self.helped = False       # missed, tone slip or hint: not a first-try answer (no Good +2)
        self.said = None          # your latest attempt, per character, for the 🎯/🎙 comparison
        if not self.queue and self.sync.signed_in and not self.round_requested:
            # round finished (or just started): send the answers, then ask YoYo what's due now
            self.round_requested = True
            finished = self.card is not None
            self.card = None
            self._show_loading("Round done ✓ — asking YoYo what's due next…" if finished
                               else "Asking YoYo which cards are due…")
            self.sync.request_due()
            return
        self.card = self.queue.pop(0) if self.queue else None
        if self.card:
            self.round_requested = False
        self.listener.reset(strip_hanzi(self.card["simplified"]) if self.card else "")
        self._show_card()

    def _show_card(self):
        self._save_settings()
        self._update_stats()
        c = self.card
        if c is None:
            self._render_hanzi(text="🎉", color=PINK)
            self.level_bar.delete("all")
            self.trad.configure(text="")
            self.english.configure(text="All caught up — nothing due on YoYo and no new cards left here. "
                                        "Pick another deck, tick “Review mastered”, or come back later.")
            self._set_status("")
            return
        if not self.locked:
            self._render_hanzi()
            self._draw_level_bar(self._level(c["code"]))
        self.trad.configure(text=c["traditional"] if c["traditional"] != c["simplified"] else "")
        if not self.locked and not self.must_repeat:
            if self.asleep:
                self._set_status(f"💤 Mic off\n{HOTKEY_NAME} or click the card to listen", DIM)
            else:
                self._set_status("")  # no prompt: the room goes to the characters
        show = self.english_var.get() or self.solved
        self.english.configure(text=c["english"] if show else "")

    def _draw_level_bar(self, level, animate=False):
        c = self.level_bar
        c.delete("all")
        right = int(c.cget("width"))
        if level < 0:
            c.create_text(right, 7, text="NEW", fill=TEAL, font=("Segoe UI", 9, "bold"), anchor="e")
            return
        color = GREEN if level > 3 else ORANGE if level > 1 else RED
        gap = 4
        w = (right - gap * (MASTERED - 1)) // MASTERED
        for i in range(MASTERED):
            x = right - (MASTERED - i) * (w + gap) + gap
            c.create_rectangle(x, 3, x + w, 11, width=0, fill=color if i < level else TRACK,
                               tags=f"seg{i}")
        if animate and level > 0:  # flash the newest bar
            seg = f"seg{level - 1}"
            self._animate("levelbar", 8, lambda i: c.itemconfigure(
                seg, fill=blend("#ffffff", color, i / 7)), ms=50)

    def _set_status(self, text, color=DIM):
        self.status.configure(text=text, fg=color)
        self.root.after_idle(self._fit_side)  # after the meaning is set too

    def _fit_side(self):
        """Wide window: with no meaning or message to show, the characters get the full width."""
        empty = not self.english.get_text().strip() and not self.status.get_text().strip()
        if empty != self.side_empty:
            self.side_empty = empty
            if self.layout_mode and self.layout_mode[0] == "wide":
                self._apply_layout(*self.layout_mode)

    def _record(self, ok, easy=False):
        """Grade like YoYo's buttons and send it: right on the first try = Good (+2),
        right after a miss, tone slip or hint = Not Bad (+1), a pass = Forgot (back to 0).
        The Good / Not Bad / Forgot buttons pick the grade themselves."""
        answer = ("easy" if easy else "good") if ok else "hard"
        code = self.card["code"]
        p = self.progress.setdefault(code, {"level": -1, "ok": 0, "fail": 0})
        p["ok" if ok else "fail"] += 1
        p["last"] = int(time.time())
        p["level"] = next_level(p.get("level", -1), answer)
        self.sync.record(code, answer)
        self._show_sync_status()
        save_json(PROGRESS_FILE, self.progress)
        self._log_day(answer)
        self._draw_level_bar(p["level"], animate=ok)

    def _apply_yoyo_due(self, codes, fetched):
        """YoYo's due list arrived: start a round with it."""
        self.due, self.due_time = codes, fetched
        self._rebuild_queue()
        if self.card is None:   # waiting on the loading screen
            if self.queue:
                self._next_card()
            else:
                self._show_card()  # nothing due and no new cards: all caught up

    def _due_fallback(self, reason=""):
        """YoYo didn't answer: study in the offline order instead of waiting."""
        if self.due is None and self.card is None:
            self.round_requested = True  # don't keep asking; Sync now or the next round will
            self._rebuild_queue()
            self._next_card()
            if reason:
                self._set_status(f"Using the offline order ({reason})", ORANGE)

    def _show_loading(self, text):
        self._render_hanzi(text="⏳", color=DIM)
        self.level_bar.delete("all")
        self.trad.configure(text="")
        self.english.configure(text="")
        self._set_status(text, DIM)

    def _log_day(self, answer=None):
        """Today's entry in the progress history: level counts as they stand now, plus
        how many answers of each grade (easy / good / hard) were given today."""
        history = load_json(HISTORY_FILE, {})
        levels = [self._level(code) for code in self.all_cards]
        # level points: every card's level added up, so any Good / Not Bad shows as progress
        points = sum(min(lv, MASTERED) for lv in levels if lv > 0)
        today = time.strftime("%Y-%m-%d")
        if today not in history:  # points_start: the day's gain when yesterday has no points yet
            history[today] = {"easy": 0, "good": 0, "hard": 0, "points_start": points}
        day = history[today]
        day["points"] = points
        day["mastered"] = sum(lv >= MASTERED for lv in levels)
        day["learning"] = sum(0 <= lv < MASTERED for lv in levels)
        day["new"] = sum(lv < 0 for lv in levels)
        if answer:
            day[answer] = day.get(answer, 0) + 1
        save_json(HISTORY_FILE, history)

    def _apply_yoyo_levels(self, levels, started, full):
        """YoYo is the source of truth, except for answers given since the pull began."""
        changed = False
        for code, level in levels.items():
            if code not in self.all_cards:
                continue
            p = self.progress.setdefault(code, {"level": -1, "ok": 0, "fail": 0})
            if p.get("last", 0) > started or p.get("level") == level:
                continue
            p["level"] = level
            changed = True
        if changed:
            save_json(PROGRESS_FILE, self.progress)
            if full and self.due is None:  # with YoYo's due list, rounds aren't reshuffled mid-way
                self._rebuild_queue()
            self._log_day()
            self._update_stats()
            if self.card and not self.locked:
                self._draw_level_bar(self._level(self.card["code"]))

    def _answer_ruby(self):
        return {i: u[4] for i, u in enumerate(card_units(self.card))}

    def _finish(self, ok, message, grade=None):
        self.solved = True
        self.english.configure(text=self.card["english"])
        if not ok:
            # Passed: reveal the answer and stay on the card until it's said correctly.
            self._record(False)
            self.must_repeat = True  # YoYo's schedule decides when it comes back (not this session)
            self._render_hanzi(ruby=self._answer_ruby(), ruby_color=PINK, reveal=True, said=self.said)
            self._set_status(message, PINK)
            self.streak = 0
            self.passed += 1
            self._update_stats()
            self._flash(PINK)
            self._shake()
            return
        self.locked = True
        if not self.must_repeat:  # repeating a passed card doesn't count as mastering it
            easy = grade == "easy" if grade else not self.helped
            self._record(True, easy=easy)
            message += "   · Good +2" if easy else "   · Not Bad +1"
            self.streak += 1
            self.right += 1
            if self.streak % 5 == 0:
                message += f"   🔥 {self.streak} in a row!"
            self._pulse_label(self.streak_lbl, YELLOW, "#ffffff")
        answer = self._answer_ruby()
        self._render_hanzi(set(range(len(answer))), ruby=answer, ruby_color=GREEN, reveal=True,
                           said=self.said)
        self._set_status(message, GREEN)
        self._update_stats()
        self._flash(GREEN)
        self._pop()
        self.advance_job = self.root.after(int(self.delay_var.get() * 1000), self._next_card)

    def _tone_slip(self, tone_off, heard_ruby):
        """Right sounds, wrong tone: shows the answer and you must say it again. It isn't
        a miss — nothing is recorded until you say it right (only "pass" counts as a miss)."""
        self.solved = True
        self.helped = True
        self.english.configure(text=self.card["english"])
        answer = self._answer_ruby()
        self._render_hanzi(set(range(len(answer))), ruby=answer, ruby_color=GREEN,
                           tone_off=set(tone_off), reveal=True, said=self.said)
        chars = strip_hanzi(self.card["simplified"])
        slips = "\n".join(f"{chars[i]} {answer[i]} — {tone_name(answer[i])} tone "
                          f"(you said {tone_name(heard_ruby[i], long=False)})"
                          for i in sorted(tone_off))
        self._set_status(f"Tone off — say it again\n{slips}", YELLOW)
        self._flash(ORANGE)
        self._shake()

    def _pass(self):
        """Always does something: fails the card, or, if it's already been passed or
        answered, moves on to the next card."""
        if not self.card:
            return
        if self.locked or self.must_repeat:
            if time.time() - self.passed_at > 1.0:  # a double-click shouldn't skip the answer
                self._next_card()
            return
        self.passed_at = time.time()
        self._finish(False, "Passed ✗ — say it to continue, or press a grade to move on")

    def _grade(self, answer):
        """The Good / Not Bad / Forgot buttons, like YoYo's: grade the card yourself.
        Forgot reveals the answer like saying "pass". Once the card is graded (said right,
        or forgotten and waiting to be said), any of them moves on."""
        if not self.card:
            return
        if self.locked or self.must_repeat:
            if time.time() - self.passed_at > 1.0:  # a double-click shouldn't skip the answer
                self._next_card()
            return
        self.listener.reset()
        if answer == "hard":
            self.passed_at = time.time()
            self._finish(False, "Forgot ✗ — say it to continue, or press a grade to move on")
        else:
            self._finish(True, "✓ Graded", grade=answer)

    def _master(self):
        """★ Master: you never want to see this card again. Sends Good (+2) three times, which
        takes any card to mastered (5) on YoYo. Asks first, since it can't be taken back here."""
        if not self.card:
            return
        card = self.card
        if not messagebox.askyesno(
                "Master this card?",
                f"{card['simplified']}  {card['pinyin']}\n\nSend Good to YoYo three times so this card is "
                "mastered and you stop seeing it?", parent=self.root):
            return
        if card is not self.card:  # the card changed while the question was up
            return
        if self.advance_job:
            self.root.after_cancel(self.advance_job)
        self.listener.reset()
        for _ in range(3):
            self._record(True, easy=True)
        self.locked = self.solved = True
        self.english.configure(text=card["english"])
        answer = self._answer_ruby()
        self._render_hanzi(set(range(len(answer))), ruby=answer, ruby_color=GREEN, reveal=True)
        self._set_status("★ Mastered — Good ×3 sent to YoYo", YELLOW)
        self._update_stats()
        self._flash(GREEN)
        self.advance_job = self.root.after(int(self.delay_var.get() * 1000), self._next_card)

    # ---------- read the card aloud

    def _speak(self):
        if not self.card:
            return
        if not self.speaker.available:
            self._set_status("No Chinese voice found. Add one in Windows Settings → Time & language → Speech.", RED)
            return
        if not self.locked:
            self.helped = True  # heard it before answering: a hint, so no Good +2
        self.speaker.say(self.card["simplified"])

    def _on_speaking(self, speaking):
        """While the app talks, don't let the mic grade its voice as your answer."""
        self.speaking = speaking
        self.speak_btn.configure(text="🔊 Speaking…" if speaking else "🔊 Hear it")
        if not speaking:
            self.deaf_until = time.time() + 0.6  # echo tail
            self.listener.reset()                 # drop the audio that heard the voice

    # ---------- mic sleep: release the mic when you're not speaking

    def _toggle_mute(self):
        self._wake() if self.asleep else self._sleep()

    def _sleep(self, idle=False):
        if self.asleep:
            return
        self.asleep = True
        self.listener.close_device()
        self.level = 0.0
        self._show_level(0)
        self._show_mic_state()
        why = "Mic off after silence (saves battery)." if idle else "Mic off."
        self._set_status(f"💤 {why}\n{HOTKEY_NAME} or click the card to listen", DIM)

    def _wake(self):
        if not self.asleep:
            return
        self.asleep = False
        self.last_heard = time.time()
        self._select_mic(initial=True)
        self.listener.reset()
        self._show_mic_state()
        self._flash(TEAL)
        if not self.locked and not self.solved:
            self._set_status("")
        elif self.solved and not self.locked:
            self._set_status("🎙 Listening — say it again", DIM)

    def _show_mic_state(self):
        self.mute_btn.base_color = RED if self.asleep else BLUE
        self.mute_btn.configure(text="Mic on" if self.asleep else "Mic off", fg=self.mute_btn.base_color)
        self.mic_icon.configure(fg=DIM if self.asleep else TEAL)

    def _sleep_tick(self):
        minutes = self.sleep_var.get()
        if not self.asleep and minutes and time.time() - self.last_heard > minutes * 60:
            self._sleep(idle=True)
        self.root.after(5000, self._sleep_tick)

    def _change_sleep(self, step):
        i = SLEEP_CHOICES.index(self.sleep_var.get()) if self.sleep_var.get() in SLEEP_CHOICES else 1
        self.sleep_var.set(SLEEP_CHOICES[max(0, min(len(SLEEP_CHOICES) - 1, i + step))])
        self.sleep_lbl.configure(text=self._sleep_text())
        self.last_heard = time.time()
        self._save_settings()

    def _sleep_text(self):
        return f"{self.sleep_var.get()} min" if self.sleep_var.get() else "never"

    # ---------- grading

    def _check(self, heard):
        """Return (correct, heard_pinyin, tone_ok, matched card indices,
        {card index: heard pinyin} to show above each character,
        card indices said with the right sounds but the wrong tone)."""
        ok, heard_py, tone_ok = self._check_whole(heard)
        units = card_units(self.card)
        heard_units = hanzi_units(heard)
        strict = False  # tone slips are caught separately (tone_off), not as misses
        if not ok:
            within = find_within(units, heard_units, strict) if units else None
            optional = optional_units(self.card)
            if within is None and optional:  # try again without the optional 儿 / bracketed part
                required = [u for i, u in enumerate(units) if i not in optional]
                within = find_within(required, heard_units, strict) if required else None
            if within is not None:  # card answer said inside a longer sentence
                ok, tone_ok = True, within
        matched, pairs = align_units(units, heard_units, strict and not ok)
        if not ok and units and len(matched) == len(units) and self._only_latin_gaps(pairs):
            ok = True  # e.g. 你觉得John明天会来吗: "John" was heard as a few hanzi
        if ok:
            matched = set(range(len(units)))
        tone_off = {i for i, j in pairs.items()
                    if i in matched and not unit_match(units[i], heard_units[j], False)[1]}
        ruby = {i: heard_units[j][3] for i, j in pairs.items()}
        if ok and len(pairs) == len(units):  # per-character tones are the better judge
            tone_ok = True
        return ok, heard_py, tone_ok and not tone_off, matched, ruby, tone_off

    def _only_latin_gaps(self, pairs):
        """All characters were said in order. The recognizer only writes hanzi, so an
        English word in the card (John, APP, OK) comes out as a few extra characters.
        Allow such extras only where the card has an English word."""
        gaps, k = set(), 0  # gaps: card character indices preceded by an English word
        for ch in self.card["simplified"]:
            if "一" <= ch <= "鿿":
                k += 1
            elif ch.isascii() and ch.isalpha():
                gaps.add(k)
        heard_at = [pairs[i] for i in range(len(pairs))]
        for i in range(1, len(heard_at)):
            extra = heard_at[i] - heard_at[i - 1] - 1
            if extra and (i not in gaps or extra > LATIN_GAP_MAX):
                return False
        return bool(gaps)

    def _check_whole(self, heard):
        """Whole-utterance check: (correct, heard_pinyin, tone_ok)."""
        c = self.card
        heard_hanzi = strip_hanzi(heard)
        heard_py = hanzi_to_pinyin(heard_hanzi)
        if heard_hanzi and heard_hanzi in (strip_hanzi(c["simplified"]), strip_hanzi(c["traditional"])):
            return True, heard_py, True
        got_letters, got_tones = normalize_pinyin(heard_py)
        if not got_letters:
            return False, heard_py, False
        best = (False, heard_py, False)
        for alt in c["pinyin"].split("/"):  # e.g. "wèi / wéi"
            want_letters, want_tones = normalize_pinyin(alt)
            if got_letters != want_letters:
                continue
            tone_ok = got_tones == want_tones
            if tone_ok:
                return True, heard_py, True
            best = (True, heard_py, False)
        return best

    def _on_partial(self, text):
        if self.locked or not self.card:
            return
        ok, heard_py, tone_ok, matched, ruby, tone_off = self._check(text)
        if ok and tone_ok:
            self.said = self._said(ruby, matched, tone_off, ok)
            self.listener.reset()
            self._finish(True, f"✓ {strip_hanzi(text)}  {heard_py}")
        else:
            if not self.solved:  # once the answer is shown, keep it up while you retry
                self._render_hanzi(matched, ruby=ruby, tone_off=tone_off)
            self._set_status(f"… {strip_hanzi(text)}  {heard_py}", VIOLET)

    def _said(self, ruby, matched, tone_off, ok):
        """Per character: (the pinyin you said, its verdict) for the 🎙 row."""
        out = {}
        for i in range(len(card_units(self.card))):
            if i in tone_off:
                out[i] = (ruby.get(i, "—"), "tone")
            elif i in ruby:
                out[i] = (ruby[i], "ok" if i in matched else "bad")
            else:  # nothing heard there: fine if it was optional (儿) and the card passed
                out[i] = ("—", "skip" if ok else "bad")
        return out

    def _card_confirmed(self, card_words, matched):
        """Did the card-limited recognizer hear the whole card, confidently, while the
        free recognizer already got at least half of it? Then the misses were the
        language model's guess, not your pronunciation."""
        chars = strip_hanzi(self.card["simplified"])
        optional = optional_units(self.card)
        without = "".join(ch for i, ch in enumerate(chars) if i not in optional)
        said = [(w, c) for w, c in card_words if w != "[unk]"]
        return (bool(chars) and "".join(w for w, _ in said) in (chars, without)
                and all(c >= GRAMMAR_CONF for _, c in said)
                and len(matched) * 2 >= len(chars))

    def _on_final(self, text, card_words=()):
        if self.locked or not self.card:
            return
        ok, heard_py, tone_ok, matched, ruby, tone_off = self._check(text)
        self.said = self._said(ruby, matched, tone_off, ok)
        shown = f"{strip_hanzi(text)}  {heard_py}"
        if not ok and self._card_confirmed(card_words, matched):
            # tones can only be judged on syllables the free recognizer got right
            ok, tone_ok = True, not tone_off
        if ok and not tone_ok:
            self._tone_slip(tone_off, ruby)
            return
        if ok:
            self._finish(True, f"✓ {shown}")
            return
        # Wrong guesses cost nothing; only "pass" fails a card. But it's no longer a first try.
        self.helped = True
        if not self.solved:
            self._render_hanzi(matched, final=True, ruby=ruby, tone_off=tone_off)
        else:  # answer already shown: keep it on top, with this attempt under it
            self._render_hanzi(reveal=True, said=self.said, **self.reveal_args)
        self._set_status(f"heard {shown} — try again", ORANGE)
        self._flash(ORANGE)
        self._shake()

    # ---------- plumbing

    def _select_mic(self, initial=False):
        name = self.mic_var.get()
        if name not in self.mic_map:
            self._set_status("No microphone found", RED)
            return
        if self.asleep:  # remember the choice; it's used when the mic turns back on
            self._save_settings()
            return
        try:
            self.listener.open_device(self.mic_map[name])
        except Exception as e:  # device busy / unplugged
            self._set_status(f"Mic error: {e}", RED)
            return
        if not initial:
            self._save_settings()
            self._set_status(f"Listening on {name}")

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "level":
                    if not self.asleep:
                        self._show_level(value)
                    continue
                if kind in ("partial", "final", "pass"):
                    if self.asleep or self.speaking or time.time() < self.deaf_until:
                        continue  # mic off, or it's hearing the app's own voice
                    self.last_heard = time.time()
                if kind == "partial":
                    self._on_partial(value)
                elif kind == "final":
                    self._on_final(*value)
                elif kind == "pass":
                    self._pass()
                elif kind == "hotkey":
                    {1: self._toggle_mute, 2: self._speak, 3: lambda: self._grade("easy"),
                     4: lambda: self._grade("good"), 5: lambda: self._grade("hard")}.get(value, lambda: None)()
                elif kind == "speaking":
                    self._on_speaking(value)
                elif kind == "yoyo_status":
                    self._show_sync_status(value)
                    if value in ("offline", "expired", "not signed in"):
                        self._due_fallback(reason={"offline": "YoYo unreachable", "expired": "sign-in expired",
                                                   "not signed in": "not signed in"}[value])
                elif kind == "yoyo_levels":
                    self._apply_yoyo_levels(*value)
                elif kind == "yoyo_due":
                    self._apply_yoyo_due(*value)
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    def _show_level(self, value):
        # fast attack, slow release so the meter and glow feel smooth
        target = min(1.0, value / 2500)
        self.level = target if target > self.level else self.level * 0.85 + target * 0.15
        lv = 0 if self.asleep else self.level
        self.meter.coords(self.meter_bar, 0, 0, 56 * lv, 12)
        glow = blend(BORDER, VIOLET, min(1.0, lv * 2.5))
        self.card_frame.configure(highlightbackground=glow, highlightcolor=glow)

    def _show_sync_status(self, state=None):
        """Header label: answers waiting to upload, or whether you're up to date with YoYo."""
        if state:
            self.sync_state = state
        state = getattr(self, "sync_state", "")
        n = self.sync.pending_count
        upload = f"↑ {n} to upload" if n else ""
        fixed = {
            "not signed in": ("☁ sign in to YoYo", RED),
            "expired": ("☁ sign-in expired", RED),
            "blocked": ("⚠ uploads stopped", RED),
            "downloading": ("↓ downloading…", DIM),
            "out of date": ("↓ out of date", ORANGE),
            "checking": ("☁ checking…", DIM),
        }
        if state in fixed:
            text, color = fixed[state]
        elif state == "offline":
            text, color = "☁ offline" + (f" · {upload}" if upload else ""), ORANGE
        elif upload:
            text, color = upload, ORANGE
        else:
            text, color = "✓ up to date", GREEN
        self.sync_lbl.configure(text=text, fg=color)

    def _open_mastery(self):
        """Open the mastery explorer (mastery.html) with the latest synced levels."""
        self._log_day()
        mastery_view.build(self.decks, self._level, self.sync.cache.get("full_pull", 0), MASTERY_FILE,
                           history=load_json(HISTORY_FILE, {}), activity=mastery_view.read_log(YOYO_LOG_FILE))
        webbrowser.open(pathlib.Path(MASTERY_FILE).as_uri())

    def _sync_clicked(self):
        if self.sync.signed_in:
            self.sync.sync_now(full=True)
            self.sync.request_due()  # also refresh what's due
        else:
            self._yoyo_sign_in()

    def _yoyo_sign_in(self):
        """Ask for the yoyochinese.com session cookie (Google/Apple/Facebook logins can't be
        done from this app, so it reuses the browser's signed-in session)."""
        win = tk.Toplevel(self.root, bg=PANEL)
        win.title("Sign in to YoYo")
        win.attributes("-topmost", True)
        win.transient(self.root)
        steps = ("1. In Chrome or Edge, sign in and open yoyochinese.com/mastery/flashcards.\n"
                 "2. Press F12, open the Network tab, type  api/v1  in its filter box,\n"
                 "    then reload the page.\n"
                 "3. Click any request that appears (e.g. “provider” or “courses”).\n"
                 "    Under Headers → Request Headers, right-click the “cookie” value → Copy value.\n"
                 "4. Paste it below and click Save. It stays on this PC (yoyo_auth.json).")
        tk.Label(win, text=steps, bg=PANEL, fg=INK, justify="left",
                 font=("Segoe UI", 10)).pack(padx=14, pady=(12, 8), anchor="w")
        entry = tk.Entry(win, width=60, bg=BTN, fg=INK, insertbackground=INK, relief="flat",
                         font=("Consolas", 10), show="•")
        entry.pack(padx=14, fill="x", ipady=4)
        entry.focus_set()

        def save():
            cookie = entry.get().strip()
            if cookie.lower().startswith("cookie:"):
                cookie = cookie[7:].strip()
            if cookie:
                self.sync.set_cookie(cookie)
                self._show_sync_status("checking")
            win.destroy()
        self._button(win, "Save", save, GREEN).pack(pady=12)
        entry.bind("<Return>", lambda e: save())

    def _save_settings(self):
        self.settings.update({
            "deck": self.deck_var.get(),
            "mic": self.mic_var.get(),
            "include_mastered": self.review_var.get(),
            "show_english": self.english_var.get(),
            "advance_s": self.delay_var.get(),
            "sleep_min": self.sleep_var.get(),
            "geometry": self.root.geometry(),
        })
        save_json(SETTINGS_FILE, self.settings)

    def _quit(self):
        self._save_settings()
        self.root.destroy()


def main():
    vosk.SetLogLevel(-1)
    try:  # crisp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
