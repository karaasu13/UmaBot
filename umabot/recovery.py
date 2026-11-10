"""What the bot does on a screen none of its pictures match, so it can be left alone for a whole day:

1. reads the words on screen and presses a safe button (Close, OK, Next, Collect All, ...);
2. keeps a copy of each new screen it had to read (logs/unknown), to add it properly later;
3. when it really can't get past a screen: a Discord message with the screenshot (and, with the Discord bot
   set up, waits for an answer such as "3" or "restart");
4. last resort: closes and reopens the game (a career in progress can be resumed), or opens it if it's gone."""
import os
from datetime import timedelta

import cv2

from .human import between
from .logger import log, notify
from .reader import decide, decide_approved


def changed(a, b):
    """Did the screen really change? (a dialog opening or closing, a new page; not a sparkle)."""
    if a is None or b is None or a.shape != b.shape:
        return True
    return float((cv2.absdiff(a, b) > 25).mean()) > 0.004


def png(img):
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else None


class Recovery:
    def __init__(self, bot, reader, game):
        self.bot, self.reader, self.game = bot, reader, game
        self.unknown_since = None      # since when no picture matches
        self.sig, self.static_since = None, None  # the screen as it last changed
        self.presses = 0               # buttons pressed by reading, on this same screen
        self.next_read = None
        self.alerted = False
        self.last_restart = None
        self.launched_at = None        # when UmaBot (re)opened the game
        self.missing_since = None
        self.text, self.text_since = None, None  # the words last read, and since when they're the same
        self.pressed = []              # (when, label) of buttons pressed by reading
        self.banned = {}               # label -> until (it was pressed in a loop)
        self.kept = []                 # small copies of the screens already saved in logs/unknown

    @property
    def cfg(self):
        return self.bot.cfg["safety"]

    # ---------------------------------------------------------------- called by the bot
    def known_screen(self):
        self.unknown_since, self.alerted, self.presses = None, False, 0
        self.text, self.text_since = None, None

    def no_window(self, now, can_act):
        """The game window isn't there: after a few minutes (while playing), open the game."""
        if self.missing_since is None:
            self.missing_since = now
        if not (can_act and self.cfg.get("restart_game")):
            return None
        gone = (now - self.missing_since).total_seconds() / 60
        recent = self.launched_at and now - self.launched_at < timedelta(minutes=10)
        if gone >= self.cfg.get("reopen_after_min", 5) and not recent:
            notify(self.bot.webhook(), "The game was closed: opening it again.")
            if self.game.start():
                self.launched_at = now
            return 60
        return None

    def window_back(self):
        self.missing_since = None

    def unknown(self, frame, now, expect_idle):
        """A screen no rule knows. Returns seconds to wait, or None for the bot's usual polling.
        expect_idle: off hours or nothing to start (veterans full): the home screen is normal then, so only
        pop-ups are dealt with, without alerts or restarts."""
        if self.unknown_since is None:
            self.unknown_since = now
        if self.text_since is None:
            self.text_since = now
        small = cv2.resize(frame.small(), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        if changed(self.sig, small):
            self.sig, self.static_since, self.presses = small, now, 0
        unknown_s = (now - self.unknown_since).total_seconds()
        static_s = (now - self.static_since).total_seconds()

        wait = self._read(frame, now, unknown_s, static_s, expect_idle)
        if wait is not None or expect_idle:
            return wait
        same_text_s = min(unknown_s, (now - self.text_since).total_seconds())
        return self._escalate(frame, now, same_text_s, static_s)

    # ---------------------------------------------------------------- 1. reading
    def _read(self, frame, now, unknown_s, static_s, expect_idle):
        c = self.cfg
        if not c.get("read_screen", True):
            return None
        if static_s < c.get("read_after_s", 8) and unknown_s < 60:
            return None  # give the screen time to settle (and the bot's pictures a chance)
        if self.next_read and now < self.next_read:
            return None
        words = self.reader.read(frame.raw)
        text = frozenset(w.key for w in words)
        if text != self.text:
            self.text, self.text_since = text, now  # the words changed: something is happening
        self.next_read = now + timedelta(seconds=45)

        # a Toughness the user allowed on Discord: confirm its dialog
        approval = getattr(self.bot, "tp_approval", None)
        if approval and now < approval[0]:
            choice = decide_approved(words, "toughness")
            if choice:
                self.next_read = now + timedelta(seconds=3)
                return self._press_word(frame, choice[1], f"confirming the Toughness: '{choice[1].text}'")
        # the steps before a run, by their words (Final Confirmation, the agenda)
        wait = self.bot.by_text(frame, words, can_start=not expect_idle)
        if wait is not None:
            self.next_read = now + timedelta(seconds=3)
            return wait
        if self.presses >= 3:
            return None
        banned = {label for label, until in self.banned.items() if now < until}
        choice = decide(words, allow_exits=not expect_idle and static_s >= 150, skip=banned)
        if choice is None:
            if self._just_launched(now) and not expect_idle:
                # title screen after (re)opening the game: a tap anywhere starts it
                self.next_read = now + timedelta(seconds=12)
                return self._tap(frame, (0.5, 0.82), "tapping the title screen")
            return None
        label, word = choice
        # the same button again and again (open, close, open...): a loop. Stop pressing it and say so.
        self.pressed = [(t, lb) for t, lb in self.pressed if now - t < timedelta(minutes=10)] + [(now, label)]
        if sum(1 for _, lb in self.pressed if lb == label) > 4:
            self.banned[label] = now + timedelta(minutes=20)
            self.pressed = [(t, lb) for t, lb in self.pressed if lb != label]
            notify(self.bot.webhook(), f"I pressed '{word.text}' over and over without getting anywhere, so I stopped "
                                       "pressing it.", image=png(frame.raw))
            return None
        self.keep(frame, label)
        self.presses += 1
        self.next_read = now + timedelta(seconds=3)
        return self._press_word(frame, word, f"unknown screen: pressing '{word.text}'")

    def _press_word(self, frame, word, what):
        x, y, w, h = word.box
        k = frame.rect[2] / frame.raw.shape[1]  # screen px per captured px (1 unless the capture was scaled)
        return self._press(frame.rect[0] + (x + w / 2) * k, frame.rect[1] + (y + h / 2) * k,
                           max(12, w * k), max(12, h * k), what)

    def _press(self, cx, cy, w, h, what):
        bot = self.bot
        bot.mouse.react()
        if bot.should_stop():
            return None
        log.info(what)
        bot.vision.bring_to_front()
        bot.mouse.click(round(cx), round(cy), round(w), round(h))
        return between(bot.rng, bot.cfg["human"]["poll_s"]) + 1.0

    def _tap(self, frame, rel, what):
        x, y = frame.relative(*rel)
        return self._press(x, y, 60, 30, what)

    def _just_launched(self, now):
        return self.launched_at is not None and now - self.launched_at < timedelta(minutes=6)

    # ---------------------------------------------------------------- 2. keeping new screens
    def keep(self, frame, tag):
        """Saves the screen in logs/unknown if it isn't like one already saved (at most 60 kept)."""
        tiny = cv2.resize(frame.small(), (96, 54), interpolation=cv2.INTER_AREA)
        if any(float(cv2.absdiff(tiny, t).mean()) < 6 for t in self.kept):
            return None
        self.kept = (self.kept + [tiny])[-60:]
        folder = os.path.join(self.bot.logs_dir, "unknown")
        os.makedirs(folder, exist_ok=True)
        files = sorted(f for f in os.listdir(folder) if f.endswith(".png"))
        for old in files[:max(0, len(files) - 59)]:
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
        safe = "".join(ch if ch.isalnum() else "_" for ch in tag)[:30]
        path = os.path.join(folder, f"{self.bot.now():%Y%m%d_%H%M%S}_{safe}.png")
        data = png(frame.raw)
        if data:
            with open(path, "wb") as f:
                f.write(data)
        return path

    # ---------------------------------------------------------------- 3 and 4. asking for help, restarting
    def _escalate(self, frame, now, same_text_s, static_s):
        """Not moving at all for a couple of minutes, or moving without its words changing for a while
        (an animation on a screen it can't get past): say so on Discord, then restart the game."""
        c = self.cfg
        stuck = static_s >= c.get("stuck_alert_min", 2) * 60 or same_text_s >= c.get("unknown_screen_min", 10) * 60
        if not stuck:
            return None
        if not self.alerted:
            self.alerted = True
            path = self.keep(frame, "stuck") or ""
            self.bot.set_status("Stuck on a screen it doesn't know", "error")
            msg = ("I'm stuck on a screen I don't know and couldn't get past it. I'll keep trying"
                   + (", and restart the game if it doesn't move." if c.get("restart_game") else ".")
                   + (f" Screenshot: {path}" if path else ""))
            if self.bot.remote is None:
                notify(self.bot.webhook(), msg, image=png(frame.raw))
            else:
                log.info(msg)  # the Discord bot sends the screenshot with the numbered words instead
        help_wait = self.bot.ask_for_help(frame, now)
        if help_wait is not None:
            return help_wait
        restart = static_s >= c.get("restart_after_min", 20) * 60 or \
            same_text_s >= (c.get("unknown_screen_min", 10) + 20) * 60
        if c.get("restart_game") and restart and \
                (self.last_restart is None or now - self.last_restart >= timedelta(minutes=45)):
            notify(self.bot.webhook(), "Still stuck: restarting the game.")
            self.last_restart = now
            if self.game.restart():
                self.launched_at = now
                self.unknown_since, self.static_since, self.sig, self.presses = now, now, None, 0
                self.text, self.text_since = None, now
                self.alerted = False
            return 60
        return between(self.bot.rng, (20, 40))
