"""Logging (console + file) and optional Discord notifications."""
import json
import logging
import os
import urllib.request
from logging.handlers import RotatingFileHandler

log = logging.getLogger("umabot")


def setup(base):
    folder = os.path.join(base, "logs")
    os.makedirs(folder, exist_ok=True)
    if log.handlers:
        return folder
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(),
              RotatingFileHandler(os.path.join(folder, "umabot.log"), maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return folder


def notify(webhook, msg):
    log.info(msg)
    if not webhook:
        return
    try:
        req = urllib.request.Request(webhook, data=json.dumps({"content": f"UmaBot: {msg}"}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "UmaBot"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:  # a failed notification must never take the bot down
        log.info(f"(Discord notification failed: {e})")
