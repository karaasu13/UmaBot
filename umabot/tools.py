"""Helper commands: download, check, test, crop, position, schedule."""
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

import cv2
import numpy as np

from .config import cli_name
from .engine import Bot, prepare_rules
from .schedule import Schedule
from .vision import Vision, load_templates

# Generic buttons from the open-source project umamusume-auto-train (Steam, 1920x1080),
# pinned to a commit so they never change. They are downloaded to your PC, not stored in this repo.
UPSTREAM = ("https://raw.githubusercontent.com/samsulpanjul/umamusume-auto-train/"
            "4b70b185bf1153769e8536e0d6737f0bebc8892c/assets/")
BUTTONS = {
    "next": "buttons/next_btn.png",
    "next2": "buttons/next2_btn.png",
    "ok": "buttons/ok_btn.png",
    "ok2": "buttons/ok_2_btn.png",
    "close": "buttons/close_btn.png",
    "cancel": "buttons/cancel_btn.png",
    "confirm": "buttons/confirm_btn.png",
    "skip": "buttons/skip_btn.png",
    "skip_big": "buttons/skip_btn_big.png",
    "complete_career": "buttons/complete_career_btn.png",
    "back": "buttons/back_btn.png",
    "career_lobby": "ui/tazuna_hint.png",
}
# Icon of the SSR Light Hello [From the Ground Up] support card (GameTora, id 30052)
CARD_URLS = [
    "https://gametora.com/images/umamusume/supports/support_card_s_30052.png",
    "https://media.gametora.com/umamusume/supports/full/small/30052.png",
]


def _fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "UmaBot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return data


def download(cfg):
    folder = os.path.join(cfg["_base"], "templates", "_downloaded")
    os.makedirs(folder, exist_ok=True)
    ok = 0
    for name, rel in BUTTONS.items():
        try:
            _fetch(UPSTREAM + rel, os.path.join(folder, name + ".png"))
            print(f"  ok     {name}")
            ok += 1
        except Exception as e:
            print(f"  ERROR  {name}: {e}")
    img, error = None, None
    for url in CARD_URLS:
        try:
            data = _fetch(url, os.path.join(folder, "card_light_hello_full.png"))
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                break
        except Exception as e:
            error = e
    if img is not None:
        h, w = img.shape[:2]
        core = img[int(h * 0.12):int(h * 0.70), int(w * 0.15):int(w * 0.85)]  # without frame/icons
        cv2.imencode(".png", core)[1].tofile(os.path.join(folder, "card_light_hello.png"))
        print(f"  ok     card_light_hello (+ _full), icon {w}x{h}")
        ok += 1
    else:
        print(f"  ERROR  Light Hello card: {error}")
        print("         Save the card image from https://gametora.com/umamusume/supports/30052-light-hello")
        print("         as templates/card_light_hello.png, or crop it from the in-game friend list.")
    print(f"\n{ok} images in {folder}")
    print(f"Next:  {cli_name()} check")


def check(cfg):
    base = cfg["_base"]
    tpls = load_templates(base)
    own_dir = os.path.join(base, "templates")
    own = {f[:-4] for f in os.listdir(own_dir) if f.endswith(".png")} if os.path.isdir(own_dir) else set()
    print(f"Templates: {len(tpls)} ({len(own)} cropped by you, {len(tpls) - len(own)} downloaded)\n")
    rules = prepare_rules(cfg["rule"], lambda n: n in tpls, cfg["card"]["templates"])
    missing = []
    for r in rules:
        extra = f"  -> missing: {', '.join(r.missing)}" if r.missing else ""
        print(f"[{'ON ' if r.enabled else 'OFF'}] {r.name}{extra}")
        missing += [t for t in r.missing if t not in missing]
    cards = cfg["card"]["templates"]
    downloadable = [t for t in missing if t in BUTTONS]
    if downloadable:
        missing = [t for t in missing if t not in BUTTONS]
        print(f"\nNot downloaded yet ({', '.join(downloadable)}):")
        print(f"  {cli_name()} download")
    card_missing = any(t in cards for t in missing)
    if card_missing:
        missing = [t for t in missing if t not in cards]
        print("\nLight Hello card missing: run 'download', or crop it from the friend list with")
        print(f"  {cli_name()} crop {cards[0]}")
    if missing:
        print("\nTo crop (with the game open):")
        for t in missing:
            print(f"  {cli_name()} crop {t}")
    if not (missing or downloadable or card_missing):
        print("\nNothing missing!")


def _capture(cfg):
    v = Vision(cfg)
    f = v.capture()
    if f is None:
        sys.exit("Game window not found. Open Umamusume (not minimised) and try again.")
    return v, f


class _NullMouse:
    def __getattr__(self, _):
        return lambda *a, **k: None


def ensure_scale(v, f):
    """Makes sure the UI scale of this window size is known. Returns (frame, description)."""
    if v.fixed_scale is not None:
        return f, f"{v.fixed_scale:.3f} (fixed in config.toml)"
    if v.calibrated:
        return f, f"{v.scale:.3f} (measured before for {f.rect[2]}x{f.rect[3]})"
    if v.maybe_calibrate(f, force=True):
        return v.make_frame(f.raw, f.rect), f"{v.scale:.3f} (measured now)"
    guess = v.scale_guess(f.rect[2], f.rect[3])
    return f, (f"{guess:.3f} (GUESS: no known button on screen to measure it; run 'test' "
               f"on a screen with a Next/OK/Close button once)")


def test(cfg):
    v, f = _capture(cfg)
    f, scale = ensure_scale(v, f)
    print(f"Window: {f.rect[2]}x{f.rect[3]} at ({f.rect[0]},{f.rect[1]})")
    print(f"UI scale: {scale}\n")
    annotated = f.img.copy()
    rows = []
    for name in v.templates:
        if name in cfg["card"]["templates"]:
            continue
        val, loc, (h, w) = v.measure(f, name)
        thr = v.thresholds.get(name, v.threshold)
        rows.append((val, name, thr))
        if val >= thr:
            cv2.rectangle(annotated, loc, (loc[0] + w, loc[1] + h), (0, 0, 255), 2)
            cv2.putText(annotated, name, (loc[0], max(12, loc[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    for val, name, thr in sorted(rows, reverse=True):
        print(f"{'SEEN' if val >= thr else '    '} {val:.3f}  {name}")

    c = cfg["card"]
    m = v.find_card(f, c["templates"], c["scales"], 0.0)
    if m:
        print(f"\nLight Hello card: best similarity {m.score:.3f} (threshold {c['threshold']})"
              f"{'  <- FOUND' if m.score >= c['threshold'] else ''}")

    bot = Bot(cfg, vision=v, mouse=_NullMouse(), sleep=lambda s: None)
    r, target = bot.evaluate(f)
    print(f"\nRule the bot would run now: {r.name + ' -> ' + str(target) if r else '(none, it would wait)'}")
    path = os.path.join(cfg["_base"], "logs", f"test_{datetime.now():%Y%m%d_%H%M%S}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imencode(".png", annotated)[1].tofile(path)
    print(f"Annotated screenshot: {path}")


def save_crop(raw, box, scale, dest):
    """Saves raw[box] converted to the reference scale, so it works at any window size."""
    x, y, w, h = box
    piece = raw[y:y + h, x:x + w]
    if abs(scale - 1) > 0.005:
        size = (max(1, round(w / scale)), max(1, round(h / scale)))
        piece = cv2.resize(piece, size, interpolation=cv2.INTER_AREA if scale > 1 else cv2.INTER_CUBIC)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cv2.imencode(".png", piece)[1].tofile(dest)
    return piece.shape[1], piece.shape[0]


def crop(cfg, name, file=None):
    """Shows a screenshot of the game (or of a file) and saves the area you select."""
    if file:
        raw = cv2.imdecode(np.fromfile(file, np.uint8), cv2.IMREAD_COLOR)
        if raw is None:
            sys.exit(f"Couldn't open {file}")
        v = Vision(cfg)
        f = v.make_frame(raw)
    else:
        print("Switch to the game, screenshot in 3 seconds...")
        time.sleep(3)
        v, f = _capture(cfg)
    f, scale = ensure_scale(v, f)
    print(f"UI scale: {scale}")
    raw = f.raw
    k = min(1.0, 1500 / raw.shape[1], 850 / raw.shape[0])
    small = cv2.resize(raw, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else raw
    print("Drag a rectangle around the button and press ENTER (C cancels).")
    x, y, w, h = cv2.selectROI(f"Crop: {name}", small, showCrosshair=True)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        sys.exit("Cancelled.")
    box = tuple(round(val / k) for val in (x, y, w, h))
    dest = os.path.join(cfg["_base"], "templates", name + ".png")
    sw, sh = save_crop(raw, box, f.k, dest)
    print(f"Saved {dest} ({sw}x{sh} at reference scale)")


def position(cfg):
    import pyautogui
    v = Vision(cfg)
    print("Move the mouse over the game (Ctrl+C to quit).")
    while True:
        f = v.capture()
        x, y = pyautogui.position()
        if f:
            fx, fy = (x - f.rect[0]) / f.rect[2], (y - f.rect[1]) / f.rect[3]
            print(f"screen ({x},{y})  ->  relative to the game [{fx:.2f}, {fy:.2f}]      ", end="\r")
        time.sleep(0.2)


def schedule(cfg):
    s = Schedule(cfg["schedule"])
    today = date.today()
    for d in range(3):
        day = today + timedelta(days=d)
        p = s.plan(day)
        breaks = ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in p["breaks"]) or "none"
        print(f"{day:%a %d/%m}: {p['start']:%H:%M} -> {p['end']:%H:%M}   breaks: {breaks}")
    state, until = s.state(datetime.now())
    print(f"\nNow: {state}" + (f" until {until:%H:%M}" if until else ""))
