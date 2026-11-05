"""Finding the game window, taking screenshots and looking for templates.

Templates are stored at the UI scale of a 1920x1080 fullscreen game ("reference scale").
The game can run at any resolution, windowed or fullscreen: the bot measures the UI scale of
the current window size (and remembers it in calibration.json), then rescales every
screenshot to the reference scale before matching.
"""
import json
import os
import sys
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .logger import log


@dataclass
class Match:
    x: int        # centre, real screen coordinates
    y: int
    w: int        # real size on screen
    h: int
    score: float


# ---------------------------------------------------------------- window (Windows)
def find_window(title):
    """((left, top, w, h) of the client area, hwnd) of the Unity window whose title contains `title`."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        if title.lower() in buf.value.lower():
            # Unity windows first (avoids grabbing a browser tab with "Umamusume" in it)
            found.append((cls.value != "UnityWndClass", buf.value.lower() != title.lower(), hwnd))
        return True

    u32.EnumWindows(callback, 0)
    if not found:
        return None
    hwnd = sorted(found)[0][2]
    if u32.IsIconic(hwnd):
        return None  # minimised
    r = wintypes.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(r))
    p = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(p))
    if r.right <= 0 or r.bottom <= 0:
        return None
    return (p.x, p.y, r.right, r.bottom), hwnd


def enable_dpi_awareness():
    """Real pixel coordinates even with Windows display scaling at 125%/150%."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


# ---------------------------------------------------------------- frames
class Frame:
    """A screenshot rescaled to the reference scale + a cache of the searches done on it."""

    def __init__(self, img, rect, vision, raw=None):
        self.img = img                          # at reference scale (what templates are matched on)
        self.raw = img if raw is None else raw  # as captured
        self.rect = rect                        # real (left, top, w, h)
        self.k = rect[2] / img.shape[1]         # real px per reference px (= UI scale)
        self.vision = vision
        self._cache = {}
        self._small = None

    def small(self):
        if self._small is None:
            self._small = shrink(self.img)
        return self._small

    def find(self, name):
        if name not in self._cache:
            self._cache[name] = self.vision.find(self, name)
        return self._cache[name]

    def sees(self, name):
        return self.find(name) is not None

    def to_screen(self, x, y, w=0, h=0):
        return Match(round(self.rect[0] + x * self.k), round(self.rect[1] + y * self.k),
                     round(w * self.k), round(h * self.k), 1.0)

    def relative(self, fx, fy):
        """Screen point from fractions (0-1) of the game area."""
        return round(self.rect[0] + fx * self.rect[2]), round(self.rect[1] + fy * self.rect[3])

    def is_black(self):
        return float(self.raw.mean()) < 3


class Vision:
    def __init__(self, cfg, source=None, clock=time.monotonic):
        g = cfg["game"]
        self.cfg = cfg
        self.ref_w, self.ref_h = g["reference_resolution"]
        self.threshold = g["threshold"]
        self.thresholds = g.get("thresholds", {})
        scale = str(g.get("scale", "auto")).strip().lower()
        self.fixed_scale = None if scale == "auto" else float(scale)
        self.templates = load_templates(cfg["_base"])
        self.card_names = set(cfg["card"]["templates"])
        self.source = source or self._capture_window
        self.clock = clock
        self.hwnd = None
        self._sct = None
        self._card_scale = {}
        self._small = {}
        # UI scale per window size
        self.calibration_path = os.path.join(cfg["_base"], "calibration.json")
        self._calibration = self._load_calibration()
        self._size = None
        self.scale = None
        self._next_sweep, self._sweep_fails = 0.0, 0

    # -- capture
    def _capture_window(self):
        if sys.platform != "win32":
            return None
        r = find_window(self.cfg["game"]["window"])
        if r is None:
            return None
        rect, self.hwnd = r
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        mon = {"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]}
        img = cv2.cvtColor(np.array(self._sct.grab(mon)), cv2.COLOR_BGRA2BGR)
        return img, rect

    def capture(self):
        r = self.source()
        if r is None:
            return None
        raw, rect = r
        return self.make_frame(raw, rect)

    def make_frame(self, raw, rect=None):
        rect = rect or (0, 0, raw.shape[1], raw.shape[0])
        s = self.current_scale(rect[2], rect[3])
        img = raw
        if abs(s - 1) > 0.005:
            size = (max(1, round(raw.shape[1] / s)), max(1, round(raw.shape[0] / s)))
            img = cv2.resize(raw, size, interpolation=cv2.INTER_AREA if s > 1 else cv2.INTER_LINEAR)
        return Frame(img, rect, self, raw=raw)

    def bring_to_front(self):
        """If another window is on top of the game, the clicks would land there."""
        if sys.platform != "win32" or not self.hwnd:
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            if u32.GetForegroundWindow() != self.hwnd:
                u32.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

    # -- UI scale
    @property
    def calibrated(self):
        return self.fixed_scale is not None or self.scale is not None

    def scale_guess(self, w, h):
        """The game's UI follows the window height (true for 16:9 at any size)."""
        return h / self.ref_h

    def current_scale(self, w, h):
        if self.fixed_scale is not None:
            return self.fixed_scale
        key = f"{w}x{h}"
        if key != self._size:  # new window size: use what we measured before, if anything
            self._size = key
            self.scale = self._calibration.get(key)
            self._next_sweep, self._sweep_fails = 0.0, 0
        return self.scale or self.scale_guess(w, h)

    def _load_calibration(self):
        try:
            with open(self.calibration_path, encoding="utf-8") as f:
                return {k: float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _set_scale(self, s, why):
        self.scale = s
        self._calibration[self._size] = round(s, 4)
        try:
            with open(self.calibration_path, "w", encoding="utf-8") as f:
                json.dump(self._calibration, f, indent=2)
        except OSError:
            pass
        log.info(f"UI scale for a {self._size} window: {s:.3f} ({why})")

    def _refine_scale(self, raw, name, s0, cx, cy):
        """Best scale (and score) for template `name` around raw point (cx, cy), within ±6% of s0."""
        tpl = self.templates[name]
        best = (0.0, s0)
        for s in np.linspace(s0 * 0.94, s0 * 1.06, 13):
            t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            th, tw = t.shape[:2]
            x0, y0 = max(0, round(cx - tw / 2 - 12)), max(0, round(cy - th / 2 - 12))
            val, _ = similarity(raw[y0:y0 + th + 24, x0:x0 + tw + 24], t)
            if val > best[0]:
                best = (val, float(s))
        return best

    def _lock_scale(self, frame, name, m):
        """A template was seen with the guessed scale: measure the exact scale on it and keep it."""
        val, s = self._refine_scale(frame.raw, name, frame.k, m.x - frame.rect[0], m.y - frame.rect[1])
        if val >= self.thresholds.get(name, self.threshold):
            self._set_scale(s, f"confirmed on '{name}'")

    def maybe_calibrate(self, frame, force=False):
        """While the UI scale is unknown, look for any known template at many scales.
        Rate-limited (20 s, backing off to 2 min), since it's heavier than a normal cycle."""
        if self.calibrated:
            return True
        now = self.clock()
        if not force and now < self._next_sweep:
            return False
        if self._sweep(frame):
            return True
        self._sweep_fails += 1
        self._next_sweep = now + min(120, 20 * 2 ** (self._sweep_fails - 1))
        return False

    def _sweep(self, frame):
        names = [n for n in self.templates if n not in self.card_names]
        if not names:
            return False
        raw_small = shrink(frame.raw)
        w, h = frame.rect[2], frame.rect[3]
        scales = [h / self.ref_h, w / self.ref_w] + list(np.geomspace(0.3, 2.2, 36))
        best = {}  # name -> (score, scale, loc)
        for s in scales:
            for n in names:
                t = cv2.resize(self._small_template(n), None, fx=s, fy=s,
                               interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
                if min(t.shape[:2]) < 8:
                    continue
                val, loc = similarity(raw_small, t)
                if val > best.get(n, (0,))[0]:
                    best[n] = (val, float(s), loc)
        for n, (val, s, loc) in sorted(best.items(), key=lambda kv: -kv[1][0])[:3]:
            if val < 0.6:
                break
            th, tw = self.templates[n].shape[:2]
            cx, cy = loc[0] * 2 + tw * s / 2, loc[1] * 2 + th * s / 2
            fval, fs = self._refine_scale(frame.raw, n, s, cx, cy)
            if fval >= self.thresholds.get(n, self.threshold):
                self._set_scale(fs, f"measured on '{n}'")
                return True
        return False

    # -- search
    def has(self, name):
        return name in self.templates

    def _small_template(self, name):
        if name not in self._small:
            self._small[name] = shrink(self.templates[name])
        return self._small[name]

    def measure(self, frame, name):
        """(score, top-left corner, (h, w)) of the best match, in reference coordinates."""
        tpl = self.templates.get(name)
        if tpl is None:
            return 0.0, (0, 0), (0, 0)
        val, loc = fast_match(frame.img, frame.small(), tpl, self._small_template(name))
        return val, loc, tpl.shape[:2]

    def find(self, frame, name, threshold=None):
        val, loc, (h, w) = self.measure(frame, name)
        threshold = threshold if threshold is not None else self.thresholds.get(name, self.threshold)
        if val < threshold:
            return None
        m = frame.to_screen(loc[0] + w / 2, loc[1] + h / 2, w, h)
        m.score = val
        if not self.calibrated and name not in self.card_names:
            self._lock_scale(frame, name, m)
        return m

    def find_in(self, frame, name, x0, y0, x1, y1, threshold=None):
        """Like find, but only inside a rectangle (reference coordinates)."""
        tpl = self.templates.get(name)
        if tpl is None:
            return None
        H, W = frame.img.shape[:2]
        x0, y0, x1, y1 = (max(0, round(x0)), max(0, round(y0)), min(W, round(x1)), min(H, round(y1)))
        val, loc = similarity(frame.img[y0:y1, x0:x1], tpl)
        if val < (threshold if threshold is not None else self.thresholds.get(name, self.threshold)):
            return None
        h, w = tpl.shape[:2]
        m = frame.to_screen(x0 + loc[0] + w / 2, y0 + loc[1] + h / 2, w, h)
        m.score = val
        return m

    def score(self, frame, name):
        return self.measure(frame, name)[0] if name in self.templates else None

    def find_card(self, frame, names, scales, threshold):
        """Multi-scale search (images from the web don't have the game's scale).
        Coarse at half resolution over every scale, then confirmed in colour at the best one."""
        best = None
        for name in names:
            tpl = self.templates.get(name)
            if tpl is None:
                continue
            if name in self._card_scale:
                s0 = self._card_scale[name]
                candidates = [s0 * f for f in (1.0, 0.95, 1.05, 0.9, 1.1)]
            else:
                candidates = list(np.linspace(scales[0], scales[1], 15))
            small = self._small_template(name)
            for s in candidates:
                t = cv2.resize(small, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
                if min(t.shape[:2]) < 8:
                    continue
                val, loc = similarity(frame.small(), t)
                if best is None or val > best[0]:
                    best = (val, loc, name, s)
        if best is None:
            return None
        _, loc, name, s = best
        tpl = self.templates[name]
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        val, loc = refine(frame.img, t, loc[0] * 2, loc[1] * 2)
        if val < threshold:
            return None
        self._card_scale[name] = s
        h, w = t.shape[:2]
        m = frame.to_screen(loc[0] + w / 2, loc[1] + h / 2, w, h)
        m.score = val
        return m


def shrink(img):
    """Half resolution, greyscale: ~10x faster for the coarse search."""
    return cv2.cvtColor(cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


def refine(img, tpl, x, y, margin=8):
    """Confirm in colour, at full resolution, only around (x, y)."""
    h, w = tpl.shape[:2]
    x0, y0 = max(0, x - margin), max(0, y - margin)
    roi = img[y0:y0 + h + 2 * margin, x0:x0 + w + 2 * margin]
    val, (dx, dy) = similarity(roi, tpl)
    return val, (x0 + dx, y0 + dy)


def fast_match(img, img_small, tpl, tpl_small):
    if min(tpl_small.shape[:2]) < 8:  # tiny template: plain full-resolution search
        return similarity(img, tpl)
    val, (x, y) = similarity(img_small, tpl_small)
    if val < 0.5:  # not even close, no point confirming
        return val, (x * 2, y * 2)
    return refine(img, tpl, x * 2, y * 2)


def similarity(img, tpl):
    """(score 0-1, top-left corner)."""
    if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
        return 0.0, (0, 0)
    if tpl.std() < 3:  # nearly flat template: CCOEFF gives false positives
        res = cv2.matchTemplate(img, tpl, cv2.TM_SQDIFF_NORMED)
        mn, _, loc, _ = cv2.minMaxLoc(res)
        val = 1.0 - mn
    else:
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, val, _, loc = cv2.minMaxLoc(res)
    if not np.isfinite(val):
        return 0.0, (0, 0)
    return float(val), loc


def load_templates(base):
    """templates/_downloaded (fallback buttons), templates/ (the crops) and templates/_card (the chosen
    support card). Later folders win on name clashes."""
    out = {}
    t = os.path.join(base, "templates")
    for folder in (os.path.join(t, "_downloaded"), t, os.path.join(t, "_card")):
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            path = os.path.join(folder, f)
            if f.lower().endswith(".png") and os.path.getsize(path) > 0:
                try:
                    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
                except cv2.error:
                    img = None  # corrupt file: ignore it
                if img is not None:
                    out[f[:-4]] = img
    return out
