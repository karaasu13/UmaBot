"""Logging (console + file) and optional Discord notifications."""
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler

log = logging.getLogger("umabot")
FORMAT = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")


def setup(base):
    folder = os.path.join(base, "logs")
    os.makedirs(folder, exist_ok=True)
    if getattr(log, "_umabot_ready", False):
        return folder
    log.setLevel(logging.INFO)
    handlers = [RotatingFileHandler(os.path.join(folder, "umabot.log"), maxBytes=1_000_000,
                                    backupCount=3, encoding="utf-8")]
    if sys.stderr is not None:  # the windowed .exe has no console
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(FORMAT)
        log.addHandler(h)
    log._umabot_ready = True
    return folder


def send_discord(webhook, text):
    """Posts a message to a Discord webhook. Returns None if it went through, else a short reason."""
    try:
        req = urllib.request.Request(webhook, data=json.dumps({"content": text}).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "UmaBot (https://github.com/karaasu13/UmaBot)"})
        urllib.request.urlopen(req, timeout=10).close()
        return None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return "Discord doesn't know that webhook (deleted, or the link is incomplete)."
        return f"Discord answered {e.code}."
    except Exception as e:
        return f"couldn't reach Discord ({e})."


def send_discord_image(webhook, text, png_bytes, filename="screen.png"):
    """Posts a message with a screenshot attached. Returns None if it went through, else a short reason."""
    boundary = "umabot" + os.urandom(8).hex()
    payload = json.dumps({"content": text}).encode()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n").encode() + payload + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{filename}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + png_bytes + f"\r\n--{boundary}--\r\n".encode()
    try:
        req = urllib.request.Request(webhook, data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                              "User-Agent": "UmaBot (https://github.com/karaasu13/UmaBot)"})
        urllib.request.urlopen(req, timeout=20).close()
        return None
    except urllib.error.HTTPError as e:
        return f"Discord answered {e.code}."
    except Exception as e:
        return f"couldn't reach Discord ({e})."


SINKS = []  # other places alerts go when there's no webhook (the Discord bot)


def notify(webhook, msg, image=None):
    """Logs msg and, if a webhook is set, posts it (with `image`, PNG bytes, attached if given)."""
    log.info(msg)
    if not webhook:
        for sink in list(SINKS):
            try:
                sink(f"UmaBot: {msg}", image)
            except Exception as e:
                log.info(f"(Discord notification failed: {e})")
        return
    if image is not None:
        problem = send_discord_image(webhook, f"UmaBot: {msg}", image)
    else:
        problem = send_discord(webhook, f"UmaBot: {msg}")
    if problem:  # a failed notification must never take the bot down
        log.info(f"(Discord notification failed: {problem})")


