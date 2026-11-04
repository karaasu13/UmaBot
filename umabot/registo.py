"""Logs (consola + ficheiro) e aviso opcional no Discord."""
import json
import logging
import os
import urllib.request
from logging.handlers import RotatingFileHandler

log = logging.getLogger("umabot")


def configurar(base):
    pasta = os.path.join(base, "logs")
    os.makedirs(pasta, exist_ok=True)
    if log.handlers:
        return pasta
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(),
              RotatingFileHandler(os.path.join(pasta, "umabot.log"), maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return pasta


def notificar(webhook, msg):
    log.info(msg)
    if not webhook:
        return
    try:
        req = urllib.request.Request(webhook, data=json.dumps({"content": f"UmaBot: {msg}"}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "UmaBot"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:  # o aviso nunca pode deitar o bot abaixo
        log.info(f"(falhou o aviso no Discord: {e})")
