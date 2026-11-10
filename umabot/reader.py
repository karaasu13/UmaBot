"""Reading the words on screen (offline OCR, free) to get past screens UmaBot has no picture of:
event pop-ups, login bonuses, missions, the title screen after a restart...

It only ever presses buttons whose label it knows to be safe (Close, OK, Next, Skip, Collect All, ...),
and on a screen that talks about carats, restoring TP, purchases or quitting it only says No / Cancel."""
import re
import threading

from .logger import log

# Labels UmaBot may press, best first. Matched against the whole text of a box (lowercase, letters only).
PROGRESS = [
    ("tap to start", r"^(tap|touch|click)(\w{0,24}(start|continue|screen))?$"),
    ("OK", r"^ok$"),
    ("Next", r"^next$"),
    ("Close", r"^close$"),
    ("Skip", r"^skip(all)?$"),
    ("To Home", r"^(backto|to)home$"),
    ("Collect All", r"^(collect|receive|claim)all$"),
    ("Collect", r"^(collect|receive|claim)$"),
    ("Continue", r"^(continue|resume)$"),
    ("Retry", r"^retry$"),
    ("Finish", r"^finish$"),
    ("Yes", r"^yes$"),
    ("Confirm", r"^confirm$"),
]
DECLINE = [
    ("No", r"^no$"),
    ("Cancel", r"^cancel$"),
    ("Close", r"^close$"),
    ("Later", r"^(later|notnow|maybelater)$"),
]
# Ways out of a screen, only when stuck for a while (they could undo something useful).
EXITS = [
    ("Back", r"^back$"),
    ("Home", r"^home$"),
]
# A screen showing any of these is about spending or losing something: only No / Cancel / Close there.
DANGER_IN_TEXT = ("carat", "jewel", "purchase", "restore", "quitthegame", "endthegame", "exitthegame",
                  "quitgame", "giveup", "delete", "retire")
DANGER_BUTTONS = re.compile(r"^(use|buy|purchase|restore|exchange|reroll|delete|sell|scout|recover)$")


def norm(text):
    return re.sub(r"[^a-z]", "", text.lower())


class Word:
    """A piece of text read on screen: (x, y, w, h) is its box on the image that was read."""

    def __init__(self, text, box, score=1.0):
        self.text, self.box, self.score = text, box, score
        self.key = norm(text)

    @property
    def center(self):
        x, y, w, h = self.box
        return x + w / 2, y + h / 2

    def __repr__(self):
        return f"Word({self.text!r})"


def decide(words, allow_exits=False):
    """(label, word) of the button to press, or None. Never a button of a spending or quitting dialog."""
    danger = any(any(d in w.key for d in DANGER_IN_TEXT) or DANGER_BUTTONS.match(w.key) for w in words)
    groups = [DECLINE] if danger else [PROGRESS, DECLINE]
    if allow_exits:
        groups.append(EXITS)
    for group in groups:
        for label, pattern in group:
            hits = [w for w in words if re.match(pattern, w.key) and w.score >= 0.6]
            if hits:
                return label, max(hits, key=lambda w: (w.box[1], w.score))  # a dialog's button: the lowest one
    return None


class Reader:
    """Loads the OCR models the first time it's needed (a second or two), then reads any screenshot."""

    def __init__(self):
        self._engine = None
        self._failed = False
        self._lock = threading.Lock()

    @property
    def available(self):
        return self._load() is not None

    def _load(self):
        with self._lock:
            if self._engine is None and not self._failed:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    self._engine = RapidOCR()
                except Exception as e:  # never take the bot down: it just can't read
                    self._failed = True
                    log.info(f"screen reading unavailable ({type(e).__name__}: {e})")
            return self._engine

    def read(self, img):
        """Words on a BGR image, top to bottom."""
        engine = self._load()
        if engine is None:
            return []
        result, _ = engine(img, use_cls=False)
        words = []
        for box, text, score in result or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            words.append(Word(text, (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), float(score)))
        return sorted(words, key=lambda w: w.box[1])
