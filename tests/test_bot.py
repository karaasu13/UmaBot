"""Tests without the game or a screen: synthetic screen, fake mouse, fake clock."""
import random
from datetime import date, datetime, timedelta

import cv2
import numpy as np
import pytest

from umabot import config
from umabot.engine import Bot, prepare_rules
from umabot.human import Mouse, click_point, mouse_path, reaction_time
from umabot.schedule import Schedule
from umabot.vision import Vision

REF = (1920, 1080)


def synthetic_screen(seed=0):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (REF[1], REF[0], 3), dtype=np.uint8)
    return cv2.GaussianBlur(img, (0, 0), 3)  # texture that survives resizing


class FakeMouse:
    def __init__(self):
        self.clicks, self.scrolls = [], []

    def click(self, x, y, w, h):
        self.clicks.append((x, y, w, h))

    def scroll(self, x, y, amount):
        self.scrolls.append((x, y, amount))

    def react(self):
        pass

    def maybe_distract(self, log=None):
        pass


@pytest.fixture
def world(tmp_path):
    """Synthetic screen + templates cropped from it (and a few that aren't on it)."""
    screen = synthetic_screen()
    other = synthetic_screen(99)
    t = tmp_path / "templates"
    t.mkdir()
    rects = {
        "next": (200, 900, 220, 60),
        "home_career": (1500, 950, 180, 80),
        "friend_list": (50, 40, 300, 50),
    }
    for name, (x, y, w, h) in rects.items():
        cv2.imwrite(str(t / f"{name}.png"), screen[y:y + h, x:x + w])
    for name in ("stop_carats", "tp_popup", "cancel"):
        cv2.imwrite(str(t / f"{name}.png"), other[100:160, 100:300])  # not on screen
    # card: on screen it shows at 60% of the size of the "web icon"
    card_rect = (700, 400, 120, 150)
    x, y, w, h = card_rect
    icon = cv2.resize(screen[y:y + h, x:x + w], None, fx=1 / 0.6, fy=1 / 0.6, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(t / "card.png"), icon)

    cfg = config.load(base=str(tmp_path))
    cfg["rule"] = [
        {"name": "stop", "if_seen": ["stop_carats"], "action": "stop"},
        {"name": "tp", "if_seen": ["tp_popup"], "click": "cancel", "action": "wait_tp"},
        {"name": "missing", "if_seen": ["does_not_exist"]},
        {"name": "start", "if_seen": ["home_career"], "starts_run": True, "counts_run": True},
        {"name": "generic", "any_of": ["next", "ok_missing"]},
    ]
    cfg["schedule"]["enabled"] = False
    state = {"img": screen}
    source = lambda: (state["img"], (0, 0, state["img"].shape[1], state["img"].shape[0]))
    return cfg, Vision(cfg, source=source), state, rects, card_rect


def make_bot(cfg, vision, now=None):
    mouse = FakeMouse()
    now = now or (lambda: datetime(2026, 11, 4, 15, 0))
    return Bot(cfg, vision=vision, mouse=mouse, now=now, sleep=lambda s: None, rng=random.Random(1)), mouse


def inside(click, rect):
    x, y, w, h = rect
    return x <= click[0] <= x + w and y <= click[1] <= y + h


# ---------------------------------------------------------------- rules
def test_rules_without_templates_are_disabled(world):
    cfg, vision, *_ = world
    rules = {r.name: r for r in prepare_rules(cfg["rule"], vision.has, cfg["card"]["templates"])}
    assert not rules["missing"].enabled and rules["missing"].missing == ["does_not_exist"]
    assert rules["generic"].enabled and rules["generic"].any_of == ["next"]


def test_invalid_action_raises(world):
    cfg, vision, *_ = world
    with pytest.raises(ValueError):
        prepare_rules([{"name": "x", "if_seen": ["next"], "action": "fly"}], vision.has, [])


def test_clicks_inside_the_button_and_counts_run(world):
    cfg, vision, _, rects, _ = world
    bot, mouse = make_bot(cfg, vision)
    assert bot.step() is not None
    assert inside(mouse.clicks[-1], rects["home_career"])  # "start" comes before "generic"
    assert bot.runs_today == 1


def test_stop_has_priority(world):
    cfg, vision, state, *_ = world
    screen = state["img"].copy()
    screen[500:560, 900:1100] = cv2.imread(cfg["_base"] + "/templates/stop_carats.png")
    state["img"] = screen
    bot, mouse = make_bot(cfg, vision)
    assert bot.step() is None and mouse.clicks == []


def test_after_hours_finishes_run_but_does_not_start_one(world):
    cfg, vision, _, rects, _ = world
    cfg["schedule"].update(enabled=True, start="10:00", end="12:00", jitter_min=0, breaks_per_day=[0, 0])
    bot, mouse = make_bot(cfg, vision, now=lambda: datetime(2026, 11, 4, 20, 0))
    bot.step()
    assert inside(mouse.clicks[-1], rects["next"])  # skipped "start", clicked the generic button
    assert bot.runs_today == 0


def test_break_takes_no_screenshots(world):
    cfg, vision, *_ = world
    cfg["schedule"].update(enabled=True, start="10:00", end="12:00", jitter_min=0,
                           finish_run_after_hours=False, breaks_per_day=[0, 0])
    vision.source = lambda: pytest.fail("should not capture")
    bot, mouse = make_bot(cfg, vision, now=lambda: datetime(2026, 11, 4, 20, 0))
    wait = bot.step()
    assert 5 <= wait <= 300 and mouse.clicks == []


def test_stuck_stops(world):
    cfg, vision, *_ = world
    cfg["safety"]["max_repeats"] = 3
    bot, _ = make_bot(cfg, vision)
    results = [bot.step() for _ in range(5)]
    assert None in results


def test_card_at_a_different_scale(world):
    cfg, vision, _, _, card_rect = world
    cfg["card"]["templates"] = ["card"]
    bot, mouse = make_bot(cfg, vision)
    bot.pick_card()
    assert mouse.clicks and inside(mouse.clicks[-1], card_rect)
    assert mouse.scrolls == []
    assert abs(vision._card_scale["card"] - 0.6) < 0.08


def test_missing_card_scrolls_and_counts_failure(world):
    cfg, vision, state, *_ = world
    cfg["card"]["templates"] = ["card"]
    state["img"] = synthetic_screen(5)
    bot, mouse = make_bot(cfg, vision)
    assert bot.pick_card() is not None
    assert len(mouse.scrolls) >= cfg["card"]["scroll_attempts"] and bot.card_failures == 1


# ---------------------------------------------------------------- vision
def test_normalises_a_smaller_window(world):
    cfg, vision, state, rects, _ = world
    state["img"] = cv2.resize(state["img"], (960, 540), interpolation=cv2.INTER_AREA)  # half-size window
    m = vision.capture().find("next")
    x, y, w, h = rects["next"]
    assert m and abs(m.x - (x + w / 2) / 2) <= 2 and abs(m.y - (y + h / 2) / 2) <= 2
    assert abs(m.w - w / 2) <= 1


# ---------------------------------------------------------------- any resolution / windowed
def letterboxed(img, scale, pad_bottom=250):
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.copyMakeBorder(small, 0, pad_bottom, 0, 0, cv2.BORDER_CONSTANT)


def test_guessed_scale_is_confirmed_and_remembered(world):
    cfg, vision, state, *_ = world
    state["img"] = cv2.resize(state["img"], (1280, 720), interpolation=cv2.INTER_AREA)
    f = vision.capture()
    assert not vision.calibrated
    assert f.find("next")  # seen with the height-based guess (720/1080)
    assert vision.calibrated and abs(vision.scale - 2 / 3) < 0.02
    again = Vision(cfg, source=vision.source)  # next run: remembered for a 1280x720 window
    again.capture()
    assert again.calibrated and again.scale == pytest.approx(vision.scale, abs=1e-3)


def test_any_aspect_ratio_gets_measured(world):
    cfg, vision, state, rects, _ = world
    state["img"] = letterboxed(state["img"], 0.7)  # window where the height-based guess is wrong
    f = vision.capture()
    assert f.find("next") is None
    assert vision.maybe_calibrate(f, force=True) and abs(vision.scale - 0.7) < 0.02
    m = vision.capture().find("next")
    x, y, w, h = rects["next"]
    assert m and abs(m.x - (x + w / 2) * 0.7) <= 3 and abs(m.y - (y + h / 2) * 0.7) <= 3


def test_bot_measures_the_scale_on_its_own(world):
    cfg, vision, state, rects, _ = world
    state["img"] = letterboxed(state["img"], 0.7)
    bot, mouse = make_bot(cfg, vision)
    assert bot.step() == 0.2 and vision.calibrated  # measured: look again right away
    bot.step()
    x, y, w, h = rects["home_career"]
    assert inside(mouse.clicks[-1], (x * 0.7, y * 0.7, w * 0.7, h * 0.7))


def test_crops_are_saved_at_reference_scale(world, tmp_path):
    from umabot.tools import save_crop
    from umabot.vision import similarity
    _, _, state, rects, _ = world
    raw = cv2.resize(state["img"], None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)  # small window
    x, y, w, h = rects["home_career"]
    box = tuple(round(v * 0.6) for v in (x, y, w, h))
    dest = str(tmp_path / "crops" / "my_button.png")
    sw, sh = save_crop(raw, box, 0.6, dest)
    assert abs(sw - w) <= 2 and abs(sh - h) <= 2
    val, loc = similarity(state["img"], cv2.imread(dest))  # matches the reference-scale screen
    assert val > 0.9 and abs(loc[0] - x) <= 3 and abs(loc[1] - y) <= 3


def test_scroll_auto_follows_the_list_title(world):
    cfg, vision, state, rects, card_rect = world
    screen = state["img"].copy()
    x, y, w, h = card_rect
    screen[y:y + h, x:x + w] = synthetic_screen(7)[y:y + h, x:x + w]  # card not on screen
    state["img"] = screen
    cfg["rule"] = [{"name": "card", "if_seen": ["friend_list"], "action": "card"}]
    cfg["card"]["templates"] = ["card"]
    bot, mouse = make_bot(cfg, vision)
    bot.step()
    fx, _, fw, _ = rects["friend_list"]
    assert mouse.scrolls and all(abs(s[0] - (fx + fw / 2)) <= 2 for s in mouse.scrolls)


# ---------------------------------------------------------------- human
def test_click_point_stays_inside_and_varies():
    rng = random.Random(0)
    pts = [click_point(500, 300, 200, 60, rng) for _ in range(5000)]
    xs, ys = zip(*pts)
    assert min(xs) >= 500 - 76 and max(xs) <= 500 + 76
    assert min(ys) >= 300 - 23 and max(ys) <= 300 + 23
    assert np.std(xs) > 20 and len(set(pts)) > 1000


def test_mouse_path_ends_on_target():
    rng = random.Random(3)
    for _ in range(50):
        pts = mouse_path(0, 0, 800, 400, rng)
        assert pts[-1] == (800, 400) and len(pts) >= 12


def test_reaction_time_within_bounds():
    rng = random.Random(0)
    v = [reaction_time(rng, (0.35, 1.6)) for _ in range(2000)]
    assert min(v) >= 0.35 and max(v) <= 1.6 and np.std(v) > 0.1


def test_mouse_with_fake_backend():
    class PG:
        def __init__(self):
            self.pos, self.events = (0, 0), []

        def position(self):
            return self.pos

        def moveTo(self, x, y):
            self.pos = (x, y)

        def mouseDown(self):
            self.events.append(("down", self.pos))

        def mouseUp(self):
            self.events.append(("up", self.pos))

        def scroll(self, amount):
            self.events.append(("scroll", amount))

    pg = PG()
    m = Mouse(config.DEFAULTS["human"], random.Random(0), backend=pg, sleep=lambda s: None)
    x, y = m.click(1000, 500, 200, 60)
    assert pg.events[-2] == ("down", (x, y)) and pg.events[-1] == ("up", (x, y))


# ---------------------------------------------------------------- schedule
def test_schedule_crosses_midnight_and_is_deterministic():
    c = dict(config.DEFAULTS["schedule"], start="10:30", end="01:00", jitter_min=45)
    s = Schedule(c, seed=42)
    p = s.plan(date(2026, 11, 4))
    assert p == s.plan(date(2026, 11, 4))
    assert p["end"].date() == date(2026, 11, 5)
    assert timedelta(hours=13) <= p["end"] - p["start"] <= timedelta(hours=16)
    for a, b in p["breaks"]:
        assert p["start"] < a < b <= p["end"]
    assert s.state(datetime(2026, 11, 5, 0, 0))[0] in ("active", "break")  # still the 4th's session
    assert s.state(datetime(2026, 11, 5, 4, 0))[0] == "off"


def test_schedule_changes_every_day():
    s = Schedule(dict(config.DEFAULTS["schedule"]), seed=7)
    starts = {s.plan(date(2026, 11, d))["start"].time() for d in range(1, 15)}
    assert len(starts) > 10


# ---------------------------------------------------------------- support card choice & settings
def test_parse_card_accepts_links_ids_and_pngs(tmp_path):
    from umabot.tools import parse_card
    assert parse_card("https://gametora.com/umamusume/supports/30052-light-hello") == (30052, "Light Hello", None)
    assert parse_card("gametora.com/ja/umamusume/supports/10083-light-hello?x=1")[0] == 10083
    assert parse_card(" 20029 ") == (20029, "", None)
    png = tmp_path / "my_card.png"
    cv2.imwrite(str(png), synthetic_screen()[:90, :70])
    assert parse_card(f'"{png}"') == (None, "my card", str(png))
    for bad in ("hello", "123", "https://gametora.com/umamusume/characters/100101", str(tmp_path / "nope.png")):
        with pytest.raises(ValueError):
            parse_card(bad)


def test_choosing_a_card_from_a_png_is_saved_and_used(world, tmp_path):
    from umabot.tools import has_card, set_card
    cfg, *_ = world
    png = tmp_path / "pick_me.png"
    cv2.imwrite(str(png), synthetic_screen(3)[:96, :72])
    assert set_card(cfg, str(png)) == "pick me"
    again = config.load(base=cfg["_base"])  # remembered in settings.json
    assert again["card"]["name"] == "pick me" and has_card(again)
    tpls = Vision(again, source=lambda: None).templates
    assert tpls["card"].shape[:2] == (96, 72)


def test_settings_override_config_and_keep_other_keys(tmp_path):
    config.save_settings(str(tmp_path), {"schedule": {"start": "09:00"}})
    config.save_settings(str(tmp_path), {"card": {"id": 30052, "name": "Light Hello (SSR)"}})
    cfg = config.load(base=str(tmp_path))
    assert cfg["schedule"]["start"] == "09:00" and cfg["schedule"]["end"] == "01:00"
    assert cfg["card"]["id"] == 30052 and cfg["card"]["templates"] == ["card", "card_full"]


def test_stop_button_stops_before_the_next_click(world):
    cfg, vision, *_ = world
    stop = {"now": False}
    mouse = FakeMouse()
    mouse.react = lambda: stop.update(now=True)  # Stop pressed while the bot is "reacting"
    bot = Bot(cfg, vision=vision, mouse=mouse, now=lambda: datetime(2026, 11, 4, 15, 0), sleep=lambda s: None,
              rng=random.Random(1), should_stop=lambda: stop["now"])
    bot.run()
    assert mouse.clicks == [] and bot.status == "Stopped"


def test_status_follows_the_schedule(world):
    cfg, vision, *_ = world
    cfg["schedule"].update(enabled=True, start="10:00", end="12:00", jitter_min=0,
                           finish_run_after_hours=False, breaks_per_day=[0, 0])
    bot, _ = make_bot(cfg, vision, now=lambda: datetime(2026, 11, 4, 20, 0))
    bot.step()
    assert bot.status.startswith("Off hours until") and bot.status_kind == "wait"


def test_out_of_tp_is_guessed_without_the_popup_picture(world):
    cfg, vision, *_ = world
    cfg["rule"] = [{"name": "Start career", "if_seen": ["home_career"], "checks_tp": True}]
    clock = {"t": datetime(2026, 11, 4, 15, 0)}
    bot, mouse = make_bot(cfg, vision, now=lambda: clock["t"])
    waits = []
    for _ in range(3):  # Start Career pressed again and again, and no run starts
        waits.append(bot.step())
        clock["t"] += timedelta(minutes=1)
    assert len(mouse.clicks) == 3
    assert waits[0] < 10 and waits[1] < 10 and 25 * 60 <= waits[2] <= 45 * 60
    assert bot.status.startswith("Out of TP")


def test_a_started_run_resets_the_tp_guess(world):
    cfg, vision, *_ = world
    cfg["rule"] = [{"name": "Start career", "if_seen": ["home_career"], "checks_tp": True}]
    clock = {"t": datetime(2026, 11, 4, 15, 0)}
    bot, _ = make_bot(cfg, vision, now=lambda: clock["t"])
    bot.step()
    bot.step()
    from umabot.engine import Rule
    confirm = Rule(name="Confirm start", if_seen=["next"], counts_run=True)  # the confirm popup appeared
    bot.execute(confirm, "next", vision.capture())
    assert bot.tp_tries == [] and bot.runs_today == 1
    assert bot.step() < 10


def test_empty_or_corrupt_templates_are_ignored(tmp_path):
    from umabot.vision import load_templates
    t = tmp_path / "templates"
    t.mkdir()
    (t / "empty.png").write_bytes(b"")
    (t / "junk.png").write_bytes(b"not a png")
    cv2.imwrite(str(t / "good.png"), synthetic_screen()[:40, :80])
    assert list(load_templates(str(tmp_path))) == ["good"]


def test_repo_config_is_valid():
    cfg = config.load()
    prepare_rules(cfg["rule"], lambda n: True, cfg["card"]["templates"])
    assert len(cfg["rule"]) >= 10
