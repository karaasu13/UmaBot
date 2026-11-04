"""Finding the game window, taking screenshots and looking for templates.

Screenshots are normalised to `reference_resolution` (e.g. 1920x1080), so templates keep
working even if the window has another size (as long as the aspect ratio is the same).
"""
import os
import sys
from dataclasses import dataclass

import cv2
import numpy as np


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
    """A normalised screenshot + a cache of the searches done on it."""

    def __init__(self, img, rect, vision):
        self.img = img
        self.rect = rect                    # real (left, top, w, h)
        self.k = rect[2] / img.shape[1]     # real px per normalised px
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
        return float(self.img.mean()) < 3


class Vision:
    def __init__(self, cfg, source=None):
        self.cfg = cfg
        self.ref_w = cfg["game"]["reference_resolution"][0]
        self.threshold = cfg["game"]["threshold"]
        self.thresholds = cfg["game"].get("thresholds", {})
        self.templates = load_templates(cfg["_base"])
        self.source = source or self._capture_window
        self.hwnd = None
        self._sct = None
        self._card_scale = {}
        self._small = {}

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
        img, rect = r
        scale = self.ref_w / img.shape[1]
        if abs(scale - 1) > 0.005:
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            img = cv2.resize(img, (self.ref_w, round(img.shape[0] * scale)), interpolation=interp)
        return Frame(img, rect, self)

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

    # -- search
    def has(self, name):
        return name in self.templates

    def _small_template(self, name):
        if name not in self._small:
            self._small[name] = shrink(self.templates[name])
        return self._small[name]

    def measure(self, frame, name):
        """(score, top-left corner, (h, w)) of the best match, in normalised coordinates."""
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
    """templates/_downloaded/*.png, then templates/*.png (your own crops win on name clashes)."""
    out = {}
    for folder in (os.path.join(base, "templates", "_downloaded"), os.path.join(base, "templates")):
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".png"):
                img = cv2.imdecode(np.fromfile(os.path.join(folder, f), np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    out[f[:-4]] = img
    return out
