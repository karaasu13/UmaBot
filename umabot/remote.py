"""Controlling UmaBot from a Discord channel (free: a Discord bot of your own, no server needed).

When UmaBot is stuck it posts the screenshot with every word numbered and a grid (columns A-H, rows 1-6).
Answer in the channel with:
  3          press word 3            C4        tap the middle of cell C4
  restart    close and reopen the game          wait      leave it alone for 30 min
  print      a new screenshot                   status    what it's doing
  pause / start                                  stop      stop UmaBot
  yes / no / always   to its questions (e.g. "Out of TP. Can I use 1 Toughness 30?")
What you press from Discord is remembered: the next time that screen shows up UmaBot presses it alone."""
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta

import cv2

from .logger import SINKS, log

API = "https://discord.com/api/v10"
COLS, ROWS = "ABCDEFGH", 6
HELP = ("Answer with a word's number (e.g. `3`), a cell (e.g. `C4`), `restart`, `wait`, `print`, `status`, "
        "`pause`, `start` or `stop`.")
YES, NO, ALWAYS = {"yes", "y", "sim", "s", "ok", "pode"}, {"no", "n", "nao", "não", "nope"}, {"always", "sempre"}


def channel_id(text):
    """The channel id from a channel link (right click the channel > Copy Link) or the id itself."""
    m = re.search(r"discord(?:app)?\.com/channels/\d+/(\d+)", text or "") or re.fullmatch(r"\s*(\d{15,22})\s*", text or "")
    return m.group(1) if m else None


class Discord:
    """The few Discord REST calls UmaBot needs."""

    def __init__(self, token, channel):
        self.token, self.channel = token.strip(), channel

    def _call(self, method, path, body=None, headers=None):
        h = {"Authorization": f"Bot {self.token}",
             "User-Agent": "DiscordBot (https://github.com/karaasu13/UmaBot, 1.0)"}
        h.update(headers or {})
        req = urllib.request.Request(API + path, data=body, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        return json.loads(data) if data else None

    def me(self):
        return self._call("GET", "/users/@me")

    def check(self):
        """None if the bot can read and write the channel, else what's wrong (for the app)."""
        try:
            self.me()
        except urllib.error.HTTPError as e:
            return "Discord doesn't accept that bot token." if e.code == 401 else f"Discord answered {e.code}."
        except Exception as e:
            return f"couldn't reach Discord ({e})."
        try:
            self._call("GET", f"/channels/{self.channel}")
            self.latest()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return "The bot can't see that channel: invite it to your server (step 4) and check the link."
            return f"Discord answered {e.code}."
        except Exception as e:
            return f"couldn't reach Discord ({e})."
        return None

    def latest(self, after=None):
        q = f"?limit=50&after={after}" if after else "?limit=1"
        msgs = self._call("GET", f"/channels/{self.channel}/messages{q}") or []
        return sorted(msgs, key=lambda m: int(m["id"]))

    def post(self, text, image=None):
        if image is None:
            body = json.dumps({"content": text}).encode()
            return self._call("POST", f"/channels/{self.channel}/messages", body, {"Content-Type": "application/json"})
        boundary = "umabot" + os.urandom(8).hex()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
                f"Content-Type: application/json\r\n\r\n").encode() + json.dumps({"content": text}).encode() + \
            (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"screen.png\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode() + image + f"\r\n--{boundary}--\r\n".encode()
        return self._call("POST", f"/channels/{self.channel}/messages", body,
                          {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def annotate(img, words):
    """The screenshot with a grid (A-H x 1-6) and each word's number, for answering from Discord."""
    out = img.copy()
    h, w = out.shape[:2]
    for i in range(1, len(COLS)):
        x = round(w * i / len(COLS))
        cv2.line(out, (x, 0), (x, h), (200, 200, 200), 1)
    for j in range(1, ROWS):
        y = round(h * j / ROWS)
        cv2.line(out, (0, y), (w, y), (200, 200, 200), 1)
    for i, c in enumerate(COLS):
        for j in range(ROWS):
            x, y = round(w * i / len(COLS)) + 3, round(h * j / ROWS) + 14
            cv2.putText(out, f"{c}{j + 1}", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(out, f"{c}{j + 1}", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    for n, word in enumerate(words, 1):
        x, y, bw, bh = (round(v) for v in word.box)
        cv2.rectangle(out, (x, y), (x + bw, y + bh), (60, 60, 230), 2)
        label = str(n)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(out, (x - 2, y - th - 8), (x + tw + 4, y - 1), (60, 60, 230), -1)
        cv2.putText(out, label, (x + 1, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def cell_center(cell, w, h):
    m = re.fullmatch(r"([A-Ha-h])([1-6])", cell.strip())
    if not m:
        return None
    i, j = COLS.index(m.group(1).upper()), int(m.group(2)) - 1
    return (i + 0.5) * w / len(COLS), (j + 0.5) * h / ROWS


class Remote:
    """Reads the channel in the background (every ~10 s) and posts there. The bot calls handle() every cycle."""

    def __init__(self, discord, interval=10.0):
        self.discord, self.interval = discord, interval
        self.commands = queue.Queue()
        self.me = None
        self.last_id = None
        self.asked = None        # (signature, words, frame) of the screen last sent for help
        self.quiet_until = None  # after "wait"
        self.paused = False
        self.question = None     # (key, when asked) of a yes/no question waiting for an answer
        self.answers = {}        # key -> "yes" | "no"
        self.always = set()      # keys answered "always" (until UmaBot is restarted)
        self._stop = threading.Event()
        self._thread = None

    # ---------------------------------------------------------------- background
    def start(self):
        try:
            self.me = self.discord.me()["id"]
            latest = self.discord.latest()
            self.last_id = latest[-1]["id"] if latest else None
        except Exception as e:
            log.info(f"Discord control unavailable ({e})")
            return False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        SINKS.append(self.say)  # alerts come here too when there's no webhook
        return True

    def close(self):
        self._stop.set()
        if self.say in SINKS:
            SINKS.remove(self.say)

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.fetch()

    def fetch(self):
        try:
            for m in self.discord.latest(self.last_id):
                self.last_id = m["id"]
                author = m.get("author", {})
                if author.get("bot") or author.get("id") == self.me or m.get("webhook_id"):
                    continue
                text = (m.get("content") or "").strip()
                if text:
                    self.commands.put(text)
        except Exception as e:
            log.info(f"(couldn't read Discord: {e})")

    def say(self, text, image=None):
        try:
            self.discord.post(text, image)
        except Exception as e:
            log.info(f"(couldn't write on Discord: {e})")

    # ---------------------------------------------------------------- called by the bot
    def ask(self, bot, frame, now):
        """Stuck: send the numbered screenshot once per screen. Returns how long to wait, or None."""
        if self.quiet_until and now < self.quiet_until:
            return 60
        small = frame.small()
        if self.asked is not None and not _changed(self.asked[0], small):
            return None  # already asked about this screen
        words = [w for w in bot.recovery.reader.read(frame.raw) if len(w.text) <= 40][:40]
        self.asked = (small, words, frame)
        ok, buf = cv2.imencode(".png", annotate(frame.raw, words))
        self.say("I'm stuck on this screen. " + HELP, buf.tobytes() if ok else None)
        return None

    def ask_yes_no(self, key, text, image=None, now=None):
        """Asks once; the answer arrives through handle(). See answer()."""
        if key in self.always:
            return
        if self.question is None or self.question[0] != key:
            self.question = (key, now)
            self.answers.pop(key, None)
            self.say(text + " Answer `yes`, `no` or `always`.", image)

    def answer(self, key):
        """'yes', 'no' or None (not answered yet). 'always' counts as yes from then on."""
        if key in self.always:
            return "yes"
        return self.answers.get(key)

    def forget(self, key):
        self.answers.pop(key, None)
        if self.question and self.question[0] == key:
            self.question = None

    def handle(self, bot, now):
        """Runs the commands that arrived. Returns seconds to wait, "stop", or None to carry on."""
        result = None
        while not self.commands.empty():
            r = self._command(bot, self.commands.get_nowait().lower(), now)
            if r is not None:
                result = r
        if result is None and self.paused:
            return 30
        return result

    def _command(self, bot, cmd, now):
        cmd = cmd.strip().strip("`").strip().rstrip("!.")
        if self.question and cmd in YES | NO | ALWAYS:
            key = self.question[0]
            if cmd in ALWAYS:
                self.always.add(key)
            self.answers[key] = "no" if cmd in NO else "yes"
            self.question = None
            self.say("OK." if cmd in NO else "OK, doing it.")
            return 0.5
        if cmd in ("stop", "quit"):
            self.say("Stopping UmaBot.")
            return "stop"
        if cmd == "pause":
            self.paused = True
            self.say("Paused. Send `start` to carry on.")
            return 30
        if cmd in ("start", "resume", "go"):
            self.paused, self.quiet_until = False, None
            self.say("Carrying on.")
            return 0.5
        if cmd == "status":
            self.say(f"{bot.status}. Runs today: {bot.runs_today}.")
            return None
        if cmd == "wait":
            self.quiet_until = now + timedelta(minutes=30)
            self.say("OK, I'll leave it alone for 30 minutes (send `start` to carry on before).")
            return 60
        if cmd == "restart":
            self.say("Restarting the game.")
            if bot.recovery.game.restart():
                bot.recovery.launched_at = now
            return 60
        if cmd in ("print", "screenshot", "ss", "screen"):
            frame = bot.vision.capture()
            if frame is None:
                self.say("I can't see the game window.")
                return None
            self.asked = None
            self.ask(bot, frame, now)
            return None
        if re.fullmatch(r"\d{1,2}", cmd) or re.fullmatch(r"[a-h][1-6]", cmd):
            return self._press(bot, cmd, now)
        if len(cmd) <= 20:
            self.say("I didn't get that. " + HELP)
        return None

    def _press(self, bot, cmd, now):
        if self.asked is None:
            self.say("Send `print` first, so we both look at the same screen.")
            return None
        sig, words, shot = self.asked
        frame = bot.vision.capture()
        if frame is None:
            self.say("I can't see the game window.")
            return None
        if _changed(sig, frame.small()):
            self.asked = None
            self.say("The screen changed since. Here's the new one:")
            self.ask(bot, frame, now)
            return None
        if cmd.isdigit():
            n = int(cmd)
            if not 1 <= n <= len(words):
                self.say(f"There's no word {n} on that screenshot.")
                return None
            x, y, w, h = words[n - 1].box
            cx, cy, label = x + w / 2, y + h / 2, words[n - 1].text
        else:
            cx, cy = cell_center(cmd, frame.raw.shape[1], frame.raw.shape[0])
            w = h = 40
            label = cmd.upper()
        k = frame.rect[2] / frame.raw.shape[1]
        bot.vision.bring_to_front()
        bot.mouse.click(round(frame.rect[0] + cx * k), round(frame.rect[1] + cy * k), round(w * k), round(h * k))
        learned = bot.learn(frame, cx * k, cy * k, label, words)
        self.asked = None
        self.say(f"Pressed {label}." + (" I'll do it alone next time." if learned else ""))
        bot.recovery.known_screen()
        return 2.0


def _changed(a, b):
    if a is None or b is None or a.shape != b.shape:
        return True
    return float((cv2.absdiff(a, b) > 25).mean()) > 0.02


def wait_for(cond, timeout=5.0):  # small helper for the tests
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.02)
    return cond()
