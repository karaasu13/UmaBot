"""Rule engine: every cycle it looks at the screen, picks the first rule that matches and runs it."""
import os
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import cv2

from .human import Mouse, between
from .logger import log, notify
from .schedule import Schedule
from .vision import Vision

ACTIONS = {"click", "stop", "card", "wait_tp", "center"}


@dataclass
class Rule:
    name: str
    if_seen: list = field(default_factory=list)
    if_not_seen: list = field(default_factory=list)
    any_of: list = field(default_factory=list)
    click: str | None = None
    action: str = "click"
    starts_run: bool = False
    counts_run: bool = False
    enabled: bool = True
    missing: list = field(default_factory=list)


def prepare_rules(rules, has, card_templates):
    """Builds the rules and disables the ones that need templates that don't exist yet."""
    out = []
    fields = Rule.__dataclass_fields__
    for d in rules:
        r = Rule(**{k: v for k, v in d.items() if k in fields and k not in ("enabled", "missing")})
        if r.action not in ACTIONS:
            raise ValueError(f"rule '{r.name}': unknown action '{r.action}' (use one of {sorted(ACTIONS)})")
        if not r.if_seen and not r.any_of:
            raise ValueError(f"rule '{r.name}': needs 'if_seen' or 'any_of'")
        required = list(r.if_seen) + ([r.click] if r.click else [])
        r.missing = [t for t in required + r.any_of + r.if_not_seen if not has(t)]
        had_any_of = bool(r.any_of)
        r.any_of = [t for t in r.any_of if has(t)]
        r.if_not_seen = [t for t in r.if_not_seen if has(t)]
        if any(not has(t) for t in required) or (had_any_of and not r.any_of):
            r.enabled = False
        if r.action == "card" and not any(has(c) for c in card_templates):
            r.enabled = False
            r.missing += [c for c in card_templates if c not in r.missing]
        out.append(r)
    return out


class Bot:
    def __init__(self, cfg, vision=None, mouse=None, now=datetime.now, sleep=time.sleep, rng=None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.vision = vision or Vision(cfg)
        self.mouse = mouse or Mouse(cfg["human"], self.rng, sleep=sleep)
        self.schedule = Schedule(cfg["schedule"])
        self.now = now
        self.sleep = sleep
        self.rules = prepare_rules(cfg["rule"], self.vision.has, cfg["card"]["templates"])
        self.logs_dir = os.path.join(cfg["_base"], "logs")
        self.last, self.repeats = None, 0
        self.day, self.runs_today = None, 0
        self.unknown_since, self.saved_unknown = None, False
        self.card_pause_until, self.card_failures = None, 0
        self.prev_state, self.no_window = None, False

    # ------------------------------------------------------------ helpers
    def webhook(self):
        return self.cfg["safety"]["discord_webhook"]

    def save_screenshot(self, frame, tag):
        if frame is None:
            return None
        os.makedirs(self.logs_dir, exist_ok=True)
        path = os.path.join(self.logs_dir, f"{tag}_{self.now():%Y%m%d_%H%M%S}.png")
        ok, buf = cv2.imencode(".png", frame.img)
        if ok:
            buf.tofile(path)
        return path

    def summary(self):
        for r in self.rules:
            if r.enabled:
                extra = f" (ignoring missing: {', '.join(r.missing)})" if r.missing else ""
                log.info(f"  [on]  {r.name}{extra}")
            else:
                log.info(f"  [off] {r.name} -> missing: {', '.join(r.missing)}")

    # ------------------------------------------------------------ decision
    def evaluate(self, frame, block_run_start=False):
        now = self.now()
        for r in self.rules:
            if not r.enabled or (block_run_start and r.starts_run):
                continue
            if r.action == "card" and self.card_pause_until and now < self.card_pause_until:
                continue
            if any(not frame.sees(t) for t in r.if_seen):
                continue
            if any(frame.sees(t) for t in r.if_not_seen):
                continue
            if r.any_of:
                target = next((t for t in r.any_of if frame.sees(t)), None)
                if target is None:
                    continue
            else:
                target = r.click or r.if_seen[0]
            return r, target
        return None, None

    def step(self):
        """One cycle. Returns how many seconds to sleep, or None to stop."""
        now = self.now()
        if now.date() != self.day:
            if self.day is not None:
                log.info(f"{self.day}: {self.runs_today} runs")
            self.day, self.runs_today = now.date(), 0

        state, until = self.schedule.state(now)
        if state != self.prev_state:
            text = {"active": "playing", "break": "on a break", "off": "off hours"}[state]
            log.info(f"-- {text}" + (f" until {until:%d/%m %H:%M}" if until else ""))
            self.prev_state = state
        if state == "break" or (state == "off" and not self.cfg["schedule"]["finish_run_after_hours"]):
            return min(300.0, max(5.0, (until - now).total_seconds()))

        frame = self.vision.capture()
        if frame is None:
            if not self.no_window:
                log.info("game window not found (is it open? minimised?)")
                self.no_window = True
            return 15
        self.no_window = False
        if frame.is_black():
            log.info("black screen: is the PC locked or the game minimised?")
            return 30

        r, target = self.evaluate(frame, block_run_start=(state != "active"))
        if r is None:
            self.last, self.repeats = None, 0
            if not self.vision.calibrated and self.vision.maybe_calibrate(frame):
                return 0.2  # UI scale just measured: look again right away
            self._unknown_screen(frame, now)
            if state == "off":
                return between(self.rng, (60, 120))
            return between(self.rng, self.cfg["human"]["poll_s"])
        self.unknown_since, self.saved_unknown = None, False

        key = (r.name, target)
        self.repeats = self.repeats + 1 if key == self.last else 1
        self.last = key
        if r.action != "card" and self.repeats > self.cfg["safety"]["max_repeats"]:
            path = self.save_screenshot(frame, "stuck")
            notify(self.webhook(), f"'{r.name}' repeated {self.repeats - 1}x without the screen changing. Stopped. ({path})")
            return None
        return self.execute(r, target, frame)

    def _unknown_screen(self, frame, now):
        if self.unknown_since is None:
            self.unknown_since = now
            return
        mins = (now - self.unknown_since).total_seconds() / 60
        if not self.saved_unknown and mins >= self.cfg["safety"]["unknown_screen_min"]:
            path = self.save_screenshot(frame, "unknown")
            log.info(f"no known screen for {mins:.0f} min, screenshot saved to {path}")
            self.saved_unknown = True

    # ------------------------------------------------------------ actions
    def execute(self, r, target, frame):
        if r.action == "stop":
            path = self.save_screenshot(frame, "stop")
            notify(self.webhook(), f"stopped: '{r.name}' ({path})")
            return None
        if r.action == "card":
            return self.pick_card(frame.find(r.if_seen[0]) if r.if_seen else None)

        self.mouse.react()
        frame = self.vision.capture()  # the screen may have changed while "reacting"
        if frame is None:
            return 2
        if r.action == "center":
            x, y = frame.relative(0.5, 0.62)
            w, h = round(frame.rect[2] * 0.25), round(frame.rect[3] * 0.12)
        else:
            m = frame.find(target)
            if m is None:
                return 0.5
            x, y, w, h = m.x, m.y, m.w, m.h
        log.info(f"[{r.name}] click '{target}'")
        self.vision.bring_to_front()
        self.mouse.click(x, y, w, h)
        if r.counts_run:
            self.runs_today += 1
            log.info(f"run #{self.runs_today} of the day started")
        if r.action == "wait_tp":
            mins = between(self.rng, self.cfg["tp"]["wait_min"])
            log.info(f"out of TP, trying again in {mins:.0f} min")
            return mins * 60
        self.mouse.maybe_distract(log.info)
        return between(self.rng, self.cfg["human"]["poll_s"])

    def scroll_point(self, frame, marker=None):
        """Where to scroll the friend list. "auto": same column as the list's title (works in any
        layout, wide fullscreen or portrait window), a bit below the middle of the window."""
        pos = self.cfg["card"]["scroll_pos"]
        if isinstance(pos, (list, tuple)):
            return frame.relative(*pos)
        x = marker.x if marker else frame.relative(0.5, 0)[0]
        return x, frame.relative(0, 0.55)[1]

    def pick_card(self, marker=None):
        c = self.cfg["card"]
        frame = None
        for i in range(c["scroll_attempts"] + 1):
            frame = self.vision.capture()
            if frame is None:
                return 5
            m = self.vision.find_card(frame, c["templates"], c["scales"], c["threshold"])
            if m:
                self.mouse.react()
                log.info(f"Light Hello found (similarity {m.score:.2f})")
                self.mouse.click(m.x, m.y, m.w, m.h)
                self.card_failures = 0
                self.card_pause_until = self.now() + timedelta(seconds=20)
                return between(self.rng, (1.5, 2.5))
            if i < c["scroll_attempts"]:
                self.mouse.scroll(*self.scroll_point(frame, marker), c["scroll_amount"])
                self.sleep(self.rng.uniform(0.8, 1.4))

        self.card_failures += 1
        log.info(f"Light Hello not found ({self.card_failures}/{c['max_failures']})")
        if self.card_failures >= c["max_failures"]:
            path = self.save_screenshot(frame, "no_card")
            notify(self.webhook(), f"couldn't find Light Hello {self.card_failures}x in a row. Stopped. ({path})")
            return None
        frame = self.vision.capture()
        m = frame.find(c["refresh"]) if frame is not None and c.get("refresh") else None
        if m:
            self.mouse.react()
            log.info("refreshing the friend list")
            self.mouse.click(m.x, m.y, m.w, m.h)
        elif frame is not None:  # scroll back to the top
            self.mouse.scroll(*self.scroll_point(frame, marker), -c["scroll_amount"] * (c["scroll_attempts"] + 2))
        return between(self.rng, (20, 40))

    # ------------------------------------------------------------ main loop
    def run(self):
        log.info(f"UmaBot starting. {len(self.vision.templates)} templates loaded. Rules:")
        self.summary()
        log.info("To stop: Ctrl+C, or fling the mouse to the top-left corner of the screen.")
        errors = 0
        while True:
            try:
                wait = self.step()
                errors = 0
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    raise
                errors += 1
                log.info("error:\n" + traceback.format_exc())
                if errors >= 20:
                    notify(self.webhook(), "20 errors in a row, stopped. See logs/umabot.log")
                    return
                wait = 10
            if wait is None:
                return
            self.sleep(wait)
