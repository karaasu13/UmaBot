"""Commands: card, test, schedule, plus the advanced ones (download, check, crop, position)."""
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from html import unescape

import cv2
import numpy as np

from .config import cli_name, save_settings
from .engine import Bot, agenda_slug, counted, prepare_rules, rule_options
from .schedule import Schedule
from .vision import Vision, load_templates

# Fallback generic buttons from the open-source project umamusume-auto-train (Steam, 1920x1080),
# pinned to a commit so they never change. Only used for buttons missing from templates/.
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

# Support cards: icons come from GameTora, by card ID (10xxx = R, 20xxx = SR, 30xxx = SSR)
GAMETORA_PAGE = "https://gametora.com/umamusume/supports/{id}"
CARD_IMAGE_URLS = (
    "https://gametora.com/images/umamusume/supports/support_card_s_{id}.png",
    "https://media.gametora.com/umamusume/supports/full/small/{id}.png",
)
EXAMPLE_CARD = "https://gametora.com/umamusume/supports/30052-light-hello"
KNOWN_CARDS = {30052: "Light Hello (SSR)"}  # cards cropped from the game, in templates/cards


def _get(url, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": "UmaBot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit) if limit else r.read()


def _write_png(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imencode(".png", img)[1].tofile(path)


# ---------------------------------------------------------------- support card
def parse_card(text):
    """GameTora link, 5-digit card ID or path to a .png -> (card_id or None, name hint, png path or None)."""
    text = text.strip().strip('"').strip("'")
    if text.lower().endswith(".png"):
        if not os.path.isfile(text):
            raise ValueError(f"file not found: {text}")
        return None, os.path.splitext(os.path.basename(text))[0].replace("_", " "), text
    m = re.search(r"supports/(\d{5})(?:-([A-Za-z0-9-]+))?", text)
    if m:
        return int(m.group(1)), (m.group(2) or "").replace("-", " ").title(), None
    if re.fullmatch(r"\d{5}", text):
        return int(text), "", None
    raise ValueError("that's not a GameTora support card link, a 5-digit card ID or a .png file")


def card_title(card_id):
    """Card name from its GameTora page, e.g. 'Light Hello (SSR)'. Best effort, '' if it fails."""
    try:
        html = _get(GAMETORA_PAGE.format(id=card_id), limit=400_000).decode("utf-8", "ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = unescape(m.group(1)).strip() if m else ""
        return re.split(r"\s+Support Card|\s+[|–-]\s+", title)[0].strip()
    except Exception:
        return ""


def card_core(img):
    """Middle of a card icon, without the frame and the rarity/level/limit-break overlays."""
    h, w = img.shape[:2]
    return img[int(h * 0.12):int(h * 0.70), int(w * 0.15):int(w * 0.85)]


def set_card(cfg, text):
    """Chooses the support card to borrow: gets its icon (or uses a .png) and saves the choice."""
    base = cfg["_base"]
    card_id, hint, path = parse_card(text)
    bundled = os.path.join(base, "templates", "cards", f"{card_id}.png") if card_id else ""
    if path or os.path.exists(bundled):
        # a picture of the card (yours, or one cropped from the game that ships with UmaBot)
        img = cv2.imdecode(np.fromfile(path or bundled, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"couldn't read {path or bundled}")
        name = hint if path else (KNOWN_CARDS.get(card_id) or card_title(card_id) or hint or f"card {card_id}")
        path = path or bundled
    else:
        img, error = None, None
        for url in CARD_IMAGE_URLS:
            try:
                img = cv2.imdecode(np.frombuffer(_get(url.format(id=card_id)), np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    break
            except Exception as e:
                error = e
        if img is None:
            raise ValueError(f"couldn't download the card's icon ({error}). "
                             "Save a picture of the card yourself and give me the .png instead.")
        name = card_title(card_id) or hint or f"card {card_id}"

    folder = os.path.join(base, "templates", "_card")
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.lower().endswith(".png"):
                os.remove(os.path.join(folder, f))
    if path:
        _write_png(img, os.path.join(folder, "card.png"))  # your own picture, used as it is
    else:
        _write_png(img, os.path.join(folder, "card_full.png"))
        _write_png(card_core(img), os.path.join(folder, "card.png"))
    save_settings(base, {"card": {"id": card_id or 0, "name": name}})
    cfg["card"]["id"], cfg["card"]["name"] = card_id or 0, name
    return name


def has_card(cfg):
    tpls = load_templates(cfg["_base"])
    return any(t in tpls for t in cfg["card"]["templates"])


def choose_card(cfg):
    """Asks which support card the bot should borrow."""
    current = cfg["card"].get("name") if has_card(cfg) else ""
    print("\nWhich support card should the bot borrow from the friend list?")
    print("Find it on GameTora and paste its link, for example")
    print(f"  {EXAMPLE_CARD}")
    print("or type its ID (e.g. 30052), or the path to a .png picture of the card.")
    if current:
        print(f"(Enter keeps the current one: {current})")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            return current
        if not text:
            if current:
                return current
            continue
        try:
            name = set_card(cfg, text)
            print(f"Support card set: {name}")
            return name
        except ValueError as e:
            print(f"  {e}")


# ---------------------------------------------------------------- fallback buttons
def missing_fallback(cfg):
    folder = os.path.join(cfg["_base"], "templates", "_downloaded")
    return [n for n in BUTTONS if not os.path.isfile(os.path.join(folder, n + ".png"))
            or os.path.getsize(os.path.join(folder, n + ".png")) == 0]


def download(cfg, quiet=False, only_missing=False):
    """Downloads the fallback generic buttons (only used for the ones missing from templates/)."""
    folder = os.path.join(cfg["_base"], "templates", "_downloaded")
    os.makedirs(folder, exist_ok=True)
    names = missing_fallback(cfg) if only_missing else list(BUTTONS)
    ok = 0
    for name in names:
        try:
            data = _get(UPSTREAM + BUTTONS[name])
            tmp = os.path.join(folder, name + ".part")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, os.path.join(folder, name + ".png"))  # never leaves a half-written file
            ok += 1
            if not quiet:
                print(f"  ok     {name}")
        except Exception as e:
            print(f"  ERROR  {name}: {e}")
    if not quiet:
        print(f"\n{ok} images in {folder}")
    return ok


# ---------------------------------------------------------------- diagnostics
def check(cfg):
    base = cfg["_base"]
    tpls = load_templates(base)
    print(f"Templates: {len(tpls)}  |  support card: {cfg['card'].get('name') or '(not chosen)'}\n")
    rules = counted(prepare_rules(cfg["rule"], lambda n: n in tpls, cfg["card"]["templates"], *rule_options(cfg)))
    missing = []
    for r in rules:
        extra = f"  -> missing: {', '.join(r.missing)}" if r.missing else ""
        print(f"[{'ON ' if r.enabled else 'OFF'}] {r.name}{extra}")
        missing += [t for t in r.missing if t not in missing]
    cards = cfg["card"]["templates"]
    downloadable = [t for t in missing if t in BUTTONS]
    card_missing = any(t in cards for t in missing)
    missing = [t for t in missing if t not in BUTTONS and t not in cards]
    if downloadable:
        print(f"\nNot downloaded yet ({', '.join(downloadable)}):  {cli_name()} download")
    if card_missing:
        print(f"\nSupport card not chosen yet:  {cli_name()} card")
    if missing:
        print(f"\nTemplates still missing: {', '.join(missing)}")
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
    """Prints what the bot sees right now and saves the screenshot (raw + annotated) to logs/."""
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
    for val, name, thr in sorted(rows, reverse=True)[:15]:
        print(f"{'SEEN' if val >= thr else '    '} {val:.3f}  {name}")

    c = cfg["card"]
    m = v.find_card(f, c["templates"], c["scales"], 0.0)
    if m:
        print(f"\nSupport card ({c.get('name') or '?'}): best similarity {m.score:.3f} (threshold {c['threshold']})"
              f"{'  <- FOUND' if m.score >= c['threshold'] else ''}")

    bot = Bot(cfg, vision=v, mouse=_NullMouse(), sleep=lambda s: None)
    r, target = bot.evaluate(f)
    print(f"\nRule the bot would run now: {r.name + ' -> ' + str(target) if r else '(none, it would wait)'}")
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    folder = os.path.join(cfg["_base"], "logs")
    _write_png(f.raw, os.path.join(folder, f"test_{stamp}_raw.png"))
    _write_png(annotated, os.path.join(folder, f"test_{stamp}.png"))
    print(f"Screenshots saved to {folder} (test_{stamp}*.png)")


# ---------------------------------------------------------------- advanced
def save_crop(raw, box, scale, dest):
    """Saves raw[box] converted to the reference scale, so it works at any window size."""
    x, y, w, h = box
    piece = raw[y:y + h, x:x + w]
    if abs(scale - 1) > 0.005:
        size = (max(1, round(w / scale)), max(1, round(h / scale)))
        piece = cv2.resize(piece, size, interpolation=cv2.INTER_AREA if scale > 1 else cv2.INTER_CUBIC)
    _write_png(piece, dest)
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


def teach_agenda(cfg, name):
    """With My Agendas open in the game: drag a box around the agenda (its name and races, not the buttons)."""
    v, f = _capture(cfg)
    f, _ = ensure_scale(v, f)
    raw = f.raw
    k = min(1.0, 1500 / raw.shape[1], 850 / raw.shape[0])
    small = cv2.resize(raw, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else raw
    x, y, w, h = cv2.selectROI(f"Drag a box around '{name}' (name and races, not the buttons), then ENTER",
                               small, showCrosshair=True)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        return False
    box = tuple(round(val / k) for val in (x, y, w, h))
    save_crop(raw, box, f.k, os.path.join(cfg["_base"], "templates", f"agenda_{agenda_slug(name)}.png"))
    return True


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
