"""Rule engine: every cycle it looks at the screen, picks the first rule that matches and runs it."""
import os
import random
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import cv2

from . import config
from .human import Mouse, between
from .logger import log, notify
from .schedule import Schedule
from .vision import Vision

ACTIONS = {"click", "stop", "card", "wait_tp", "center", "trainee", "agenda"}


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
    checks_tp: bool = False      # pressing it starts a run only if there's enough TP
    once_per_run: bool = False   # fires at most once between two runs
    only_if: str | None = None   # only used when this option is on (see rule_options)
    notify: str | None = None    # message shown in the app (and Discord) when it fires
    optional: bool = False       # rare screen: not counted as missing while its picture isn't there
    enabled: bool = True
    unused: bool = False         # switched off by an option, not missing anything
    missing: list = field(default_factory=list)


def counted(rules):
    """Rules that count for "screens ready": in use, and not an optional one that isn't set up."""
    return [r for r in rules if not r.unused and not (r.optional and not r.enabled)]


class _Keep(dict):
    def __missing__(self, key):  # unknown {placeholder}: leave it as it is
        return "{" + key + "}"


def agenda_slug(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def rule_options(cfg):
    """Values the rules can refer to: {focus} in template names, and only_if switches."""
    t = cfg["training"]
    slug = agenda_slug(t.get("agenda"))
    subs = {"focus": t.get("focus", "sprint"), "agenda": slug}
    flags = {"load_agenda": bool(slug)}
    return subs, flags


def prepare_rules(rules, has, card_templates, subs=None, flags=None):
    """Builds the rules and disables the ones that need templates that don't exist yet."""
    out = []
    subs, flags = subs or {}, flags or {}
    fields = Rule.__dataclass_fields__
    fill = lambda t: t.format_map(_Keep(subs)) if t and "{" in t else t
    for d in rules:
        r = Rule(**{k: v for k, v in d.items() if k in fields and k not in ("enabled", "missing", "unused")})
        if r.action not in ACTIONS:
            raise ValueError(f"rule '{r.name}': unknown action '{r.action}' (use one of {sorted(ACTIONS)})")
        if not r.if_seen and not r.any_of:
            raise ValueError(f"rule '{r.name}': needs 'if_seen' or 'any_of'")
        r.if_seen, r.if_not_seen, r.any_of = ([fill(t) for t in lst] for lst in (r.if_seen, r.if_not_seen, r.any_of))
        r.click = fill(r.click)
        if r.only_if and not flags.get(r.only_if):
            r.enabled, r.unused = False, True
            out.append(r)
            continue
        required = list(r.if_seen) + ([r.click] if r.click else [])
        if r.action == "agenda":
            required.append(f"agenda_{subs.get('agenda', '')}")
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
    def __init__(self, cfg, vision=None, mouse=None, now=datetime.now, sleep=time.sleep, rng=None,
                 should_stop=lambda: False):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.vision = vision or Vision(cfg)
        self.mouse = mouse or Mouse(cfg["human"], self.rng, sleep=sleep)
        self.schedule = Schedule(cfg["schedule"])
        self.now = now
        self.sleep = sleep
        self.should_stop = should_stop
        self.rules = prepare_rules(cfg["rule"], self.vision.has, cfg["card"]["templates"], *rule_options(cfg))
        self.logs_dir = os.path.join(cfg["_base"], "logs")
        self.last, self.repeats = None, 0
        self.day, self.runs_today = None, 0
        self.run_times = []  # when each of today's runs started
        self.tp_tries = []   # recent presses of a checks_tp button that didn't start a run
        self.done_this_run = set()  # once_per_run rules already used
        self.notices = []    # messages for the app
        self.unknown_since, self.saved_unknown = None, False
        self.card_pause_until, self.card_failures = None, 0
        self.prev_state, self.no_window = None, False
        self.status, self.status_kind = "Starting", "ok"  # kind: ok | wait | stop | error

    # ------------------------------------------------------------ helpers
    def set_status(self, text, kind="ok"):
        self.status, self.status_kind = text, kind

    @property
    def card_name(self):
        return self.cfg["card"].get("name") or "the support card"

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
        used = counted(self.rules)
        ready = [r for r in used if r.enabled]
        log.info(f"screens ready: {len(ready)}/{len(used)}")
        off = [r.name for r in used if not r.enabled]
        if off:
            log.info("not set up yet (skipped): " + "; ".join(off))

    # ------------------------------------------------------------ decision
    def evaluate(self, frame, block_run_start=False):
        now = self.now()
        for r in self.rules:
            if not r.enabled or (block_run_start and r.starts_run):
                continue
            if r.action == "card" and self.card_pause_until and now < self.card_pause_until:
                continue
            if r.once_per_run and r.name in self.done_this_run:
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
            self.run_times = []

        state, until = self.schedule.state(now)
        if state != self.prev_state:
            text = {"active": "Playing", "break": "On a break", "off": "Off hours"}[state]
            log.info(text + (f" until {until:%H:%M}" if until else ""))
            self.prev_state = state
        if state == "break":
            self.set_status(f"On a break until {until:%H:%M}", "wait")
        elif state == "off":
            self.set_status(f"Off hours until {until:%H:%M}", "wait")
        else:
            self.set_status("Playing")
        if state == "break" or (state == "off" and not self.cfg["schedule"]["finish_run_after_hours"]):
            return min(300.0, max(5.0, (until - now).total_seconds()))

        frame = self.vision.capture()
        if frame is None:
            if not self.no_window:
                log.info("game window not found (is it open? minimised?)")
                self.no_window = True
            self.set_status("Waiting for the game window", "wait")
            return 15
        self.no_window = False
        if frame.is_black():
            log.info("black screen: is the PC locked or the game minimised?")
            self.set_status("Black screen (PC locked?)", "wait")
            return 30

        full = self.veterans_free() == 0
        if full:
            self.set_status("Veterans list full", "error")
            msg = "The veterans list is full. Make room in the game, update the free spaces in UmaBot, then press Start."
            if msg not in self.notices:
                self.notices.append(msg)
                notify(self.webhook(), msg)
        r, target = self.evaluate(frame, block_run_start=(state != "active" or full))
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
            self.set_status("Stopped: stuck on the same screen", "error")
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
            self.set_status(f"Stopped: {r.name}", "error")
            notify(self.webhook(), f"stopped: '{r.name}' ({path})")
            return None
        if r.once_per_run:
            self.done_this_run.add(r.name)
        if r.notify:
            self.notices.append(r.notify)
            notify(self.webhook(), r.notify)
        if r.action == "card":
            return self.pick_card(frame.find(r.if_seen[0]) if r.if_seen else None)
        if r.action == "trainee":
            return self.pick_trainee()
        if r.action == "agenda":
            return self.load_agenda(frame.find(r.if_seen[0]) if r.if_seen else None)

        self.mouse.react()
        if self.should_stop():
            return None
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
            self.run_times.append(self.now())
            self.tp_tries = []
            self.done_this_run = set()
            self._use_veteran_slot()
            log.info(f"run #{self.runs_today} of the day started")
        if r.checks_tp and self._looks_out_of_tp():
            r = Rule(name=r.name, if_seen=r.if_seen, action="wait_tp")
            log.info(f"'{r.name}' was pressed {self.cfg['tp']['guess_after']} times without a run starting: "
                     "probably out of TP")
        if r.action == "wait_tp":
            if self.run_times and (self.now() - self.run_times[-1]).total_seconds() < 60:
                self.run_times.pop()  # that "run" never started: the TP window came up instead
                self.runs_today -= 1
            mins = between(self.rng, self.cfg["tp"]["wait_min"])
            log.info(f"out of TP, trying again in {mins:.0f} min")
            self.set_status(f"Out of TP, retrying at {self.now() + timedelta(minutes=mins):%H:%M}", "wait")
            return mins * 60
        self.mouse.maybe_distract(log.info)
        return between(self.rng, self.cfg["human"]["poll_s"])

    def veterans_free(self):
        v = self.cfg["training"].get("veterans_free", -1)
        return v if isinstance(v, int) and v >= 0 else None

    def _use_veteran_slot(self):
        free = self.veterans_free()
        if free is None:
            return
        free = max(0, free - 1)
        self.cfg["training"]["veterans_free"] = free
        config.save_settings(self.cfg["_base"], {"training": {"veterans_free": free}})
        if free <= 5:
            msg = (f"Only {free} space{'s' if free != 1 else ''} left in the veterans list. "
                   "Make room before it fills up." if free else
                   "This run fills the veterans list. Make room before the next one.")
            self.notices.append(msg)
            notify(self.webhook(), msg)

    def _click_match(self, m, what):
        self.mouse.react()
        if self.should_stop():
            return None
        log.info(what)
        self.vision.bring_to_front()
        self.mouse.click(m.x, m.y, m.w, m.h)
        return between(self.rng, self.cfg["human"]["poll_s"])

    def pick_trainee(self):
        """Trainee select: keep the trainee if it's one of the preferred ones, otherwise pick the first
        preferred one in view, then Next."""
        frame = self.vision.capture()
        if frame is None:
            return 2
        prefs = [p for p in self.cfg["training"].get("trainees", []) if p]
        for p in prefs:
            if frame.sees(f"trainee_{p}_banner"):
                m = frame.find("next")
                return self._click_match(m, f"trainee: {p.replace('_', ' ').title()}, next") if m else 1
        for p in prefs:
            m = frame.find(f"trainee_{p}_icon")
            if m:
                return self._click_match(m, f"choosing {p.replace('_', ' ').title()} as the trainee")
        m = frame.find("next")
        if prefs and m:
            msg = "None of your preferred trainees was in view, so the run uses the one that was selected."
            if msg not in self.notices:
                self.notices.append(msg)
                notify(self.webhook(), msg)
        return self._click_match(m, "next") if m else 1

    def load_agenda(self, marker=None):
        """My Agendas: find the saved agenda (scrolling if needed) and press its Load List."""
        name = f"agenda_{agenda_slug(self.cfg['training'].get('agenda'))}"
        tpl = self.vision.templates.get(name)
        frame = None
        for i in range(6):
            frame = self.vision.capture()
            if frame is None:
                return 2
            m = frame.find(name)
            if m and tpl is not None:
                # Load List sits just right of the agenda's box (Save Here is above it: never pressed)
                x0 = (m.x - frame.rect[0]) / frame.k + tpl.shape[1] / 2
                y0 = (m.y - frame.rect[1]) / frame.k - tpl.shape[0] / 2
                lm = self.vision.find_in(frame, "load_list", x0 - 10, y0 - 10, x0 + 260, y0 + tpl.shape[0] + 60)
                if lm:
                    return self._click_match(lm, f"loading the agenda '{self.cfg['training']['agenda']}'")
                break
            if i < 5:
                self.mouse.scroll(*self.scroll_point(frame, marker), -6)
                self.sleep(self.rng.uniform(0.45, 0.75))
        msg = (f"Couldn't find the agenda '{self.cfg['training'].get('agenda')}' in My Agendas, "
               "so this run keeps the agenda that was set.")
        self.notices.append(msg)
        notify(self.webhook(), msg)
        return 1

    def _looks_out_of_tp(self):
        """Without a picture of the "not enough TP" popup: if Start Career keeps being pressed and no run
        starts, the game is showing that popup."""
        tp = self.cfg["tp"]
        if tp["guess_after"] <= 0:
            return False
        now = self.now()
        window = timedelta(minutes=tp["guess_window_min"])
        self.tp_tries = [t for t in self.tp_tries if now - t < window] + [now]
        if len(self.tp_tries) >= tp["guess_after"]:
            self.tp_tries = []
            return True
        return False

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
            if self.should_stop():
                return None
            frame = self.vision.capture()
            if frame is None:
                return 5
            m = self.vision.find_card(frame, c["templates"], c["scales"], c["threshold"])
            if m:
                self.mouse.react()
                if self.should_stop():
                    return None
                log.info(f"{self.card_name} found (similarity {m.score:.2f})")
                self.mouse.click(m.x, m.y, m.w, m.h)
                self.card_failures = 0
                self.card_pause_until = self.now() + timedelta(seconds=20)
                return between(self.rng, (1.5, 2.5))
            if i < c["scroll_attempts"]:
                before = frame.small()
                self.mouse.scroll(*self.scroll_point(frame, marker), c["scroll_amount"])
                self.sleep(self.rng.uniform(0.45, 0.75))
                after = self.vision.capture()
                if after is not None and after.small().shape == before.shape and \
                        float(cv2.absdiff(after.small(), before).mean()) < 0.8:
                    break  # the list didn't move: we're at the bottom

        self.card_failures += 1
        log.info(f"{self.card_name} not found ({self.card_failures}/{c['max_failures']})")
        if self.card_failures >= c["max_failures"]:
            path = self.save_screenshot(frame, "no_card")
            self.set_status(f"Stopped: {self.card_name} not in the friend list", "error")
            notify(self.webhook(), f"couldn't find {self.card_name} {self.card_failures}x in a row. Stopped. ({path})")
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
        t = self.cfg["training"]
        trainees = " or ".join(p.replace("_", " ").title() for p in t.get("trainees", [])) or "the selected trainee"
        agenda = f"agenda '{t['agenda']}'" if t.get("agenda") else "the agenda already set in the game"
        log.info(f"UmaBot starting: {trainees}, {t.get('focus', 'sprint')} focus, {agenda}, borrowing {self.card_name}.")
        self.summary()
        log.info("To stop: press Stop, or fling the mouse to the top-left corner of the screen.")
        errors = 0
        while not self.should_stop():
            try:
                wait = self.step()
                errors = 0
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    raise
                errors += 1
                log.info("error:\n" + traceback.format_exc())
                if errors >= 20:
                    self.set_status("Stopped: too many errors", "error")
                    notify(self.webhook(), "20 errors in a row, stopped. See logs/umabot.log")
                    return
                wait = 10
            if wait is None:
                break
            self.sleep(wait)
        if self.should_stop():
            self.set_status("Stopped", "stop")
