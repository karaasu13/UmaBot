"""Everything that makes the mouse look like a person: click point, curved path, timings."""
import math
import random
import time


def between(rng, pair):
    return rng.uniform(pair[0], pair[1])


def click_point(cx, cy, w, h, rng=random):
    """Random point inside the button: most likely near the centre,
    but spread over ~75% of its area (never on the edges)."""
    def sample(mid, size):
        limit = size * 0.38
        while True:
            d = rng.gauss(0, size * 0.17)
            if abs(d) <= limit:
                return mid + d
    return round(sample(cx, w)), round(sample(cy, h))


def reaction_time(rng, bounds):
    """Log-normal distribution (like real reaction times), clipped to the bounds."""
    lo, hi = bounds
    mu = math.log(max(1e-3, (lo + hi) / 2.6))
    return min(hi, max(lo, rng.lognormvariate(mu, 0.45)))


def mouse_path(x0, y0, x1, y1, rng=random):
    """Bézier curve with ease-in/ease-out and an occasional overshoot."""
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 3:
        return [(x1, y1)]
    n = max(12, min(70, int(dist / 14)))
    px, py = -(y1 - y0) / dist, (x1 - x0) / dist  # perpendicular
    d1 = rng.uniform(-0.22, 0.22) * dist
    d2 = d1 * rng.uniform(0.1, 0.9)
    t1, t2 = rng.uniform(0.2, 0.45), rng.uniform(0.55, 0.85)
    c1 = (x0 + (x1 - x0) * t1 + px * d1, y0 + (y1 - y0) * t1 + py * d1)
    c2 = (x0 + (x1 - x0) * t2 + px * d2, y0 + (y1 - y0) * t2 + py * d2)

    pts = []
    for i in range(1, n + 1):
        t = i / n
        t = t * t * (3 - 2 * t)  # ease-in-out
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        x = a * x0 + b * c1[0] + c * c2[0] + d * x1
        y = a * y0 + b * c1[1] + c * c2[1] + d * y1
        pts.append((round(x), round(y)))

    if dist > 150 and rng.random() < 0.15:  # overshoot slightly, then correct
        ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
        o = rng.uniform(3, 9)
        pts[-1:] = [(round(x1 + ux * o), round(y1 + uy * o)), (x1, y1)]
    pts[-1] = (x1, y1)
    return pts


class Mouse:
    """Wraps pyautogui with human-like movement. `backend` allows tests without a screen."""

    def __init__(self, cfg_human, rng=None, backend=None, sleep=time.sleep):
        self.cfg = cfg_human
        self.rng = rng or random.Random()
        self.sleep = sleep
        if backend is None:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0
            backend = pyautogui
        self.pg = backend
        self.busy = False          # True while UmaBot itself moves the mouse
        self.done_at = time.monotonic()

    def move(self, x, y):
        x0, y0 = self.pg.position()
        pts = mouse_path(x0, y0, x, y, self.rng)
        dist = math.hypot(x - x0, y - y0)
        duration = self.rng.uniform(0.07, 0.13) + 0.0002 * dist + abs(self.rng.gauss(0, 0.025))
        step = duration / len(pts)
        for px, py in pts:
            self.pg.moveTo(px, py)
            self.sleep(step)

    def click(self, cx, cy, w, h):
        self.busy = True
        try:
            x, y = click_point(cx, cy, w, h, self.rng)
            self.move(x, y)
            self.sleep(self.rng.uniform(0.02, 0.07))
            self.pg.mouseDown()
            self.sleep(between(self.rng, self.cfg["hold_ms"]) / 1000)
            self.pg.mouseUp()
            return x, y
        finally:
            self.busy, self.done_at = False, time.monotonic()

    def scroll(self, x, y, amount):
        self.busy = True
        try:
            self.move(x + self.rng.randint(-15, 15), y + self.rng.randint(-15, 15))
            direction = -1 if amount < 0 else 1
            for _ in range(abs(amount)):  # several wheel notches, with an irregular rhythm
                self.pg.scroll(direction * self.rng.randint(1, 2))
                self.sleep(self.rng.uniform(0.02, 0.06))
        finally:
            self.busy, self.done_at = False, time.monotonic()

    def react(self):
        """Wait before clicking, like someone who has just noticed the button."""
        self.sleep(reaction_time(self.rng, self.cfg["reaction_s"]))

    def maybe_distract(self, log=None):
        if self.rng.random() < self.cfg["distraction_chance"]:
            s = between(self.rng, self.cfg["distraction_s"])
            if log:
                log(f"short break of {s:.0f}s")
            self.sleep(s)
