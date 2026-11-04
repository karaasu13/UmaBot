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
    cv2.imwrite(str(t / "card_light_hello.png"), icon)

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
    cfg["card"]["templates"] = ["card_light_hello"]
    bot, mouse = make_bot(cfg, vision)
    bot.pick_card()
    assert mouse.clicks and inside(mouse.clicks[-1], card_rect)
    assert mouse.scrolls == []
    assert abs(vision._card_scale["card_light_hello"] - 0.6) < 0.08


def test_missing_card_scrolls_and_counts_failure(world):
    cfg, vision, state, *_ = world
    cfg["card"]["templates"] = ["card_light_hello"]
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


def test_repo_config_is_valid():
    cfg = config.load()
    prepare_rules(cfg["rule"], lambda n: True, cfg["card"]["templates"])
    assert len(cfg["rule"]) >= 10
