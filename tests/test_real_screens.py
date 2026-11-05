"""A whole real run (screenshots from the Steam game, window 1106x622): on every screen the bot must pick
the right button, and never anything that spends TP or carats."""
import glob
import os
import shutil

import cv2
import pytest

from umabot import config, tools
from umabot.engine import Bot
from umabot.vision import Vision

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(REPO, "tests", "screens")
SCALE = 622 / 1080

# screen -> (expected template to click, or the rule name for actions that decide later)
START = {
    "s01_home": "home_career",
    "s02_scenario": "next",
    "s03_trainee_oguri": "Choose the trainee",
    "s04_legacy_empty": "auto_select",
    "s05_autoselect_dialog": "ok",
    "s06_deck_no_friend": "empty_friend_slot",
    "s07_borrow_list": "Pick the support card",
    "s08_borrow_list_light_hello": "Pick the support card",
    "s09_deck_ready": "start_career",
    "s10_final_normal": "it_tab_off",
    "s11_final_independent": "it_start",
    "s12_agenda_empty": "close",
    "s13_my_agendas": "close",
    "s14_agenda_loaded": "close",
    "s15_trainee_condor": "Choose the trainee",
    "s16_recover_tp": "close",
}
END = {
    "10_training_log": "ok",
    "11_complete_career": "complete_career",
    "12_finish_dialog": "finish",
    "13_career_rank": "next",
    "14_sparks": "confirm",
    "15_sparks_confirm": "confirm",
    "16_details": "close",
    "17_record": "next",
    "18_rewards_fans": "next",
    "19_rewards_cards": "next",
    "20_career_complete": "to_home",
}


class FakeMouse:
    def __init__(self):
        self.clicks, self.scrolls = [], []

    def click(self, x, y, w, h):
        self.clicks.append((x, y))

    def scroll(self, x, y, amount):
        self.scrolls.append((x, y, amount))

    def react(self):
        pass

    def maybe_distract(self, log=None):
        pass


@pytest.fixture()
def base(tmp_path):
    shutil.copytree(os.path.join(REPO, "templates"), tmp_path / "templates")  # only the repo's templates
    shutil.copy(os.path.join(REPO, "config.toml"), tmp_path / "config.toml")
    tools.set_card(config.load(base=str(tmp_path)), "30052")  # Light Hello, as chosen in the app
    return str(tmp_path)


def bot_on(base, screen, **training):
    cfg = config.load(base=base)
    cfg["training"].update(training)
    img = cv2.imread(os.path.join(SCREENS, screen + ".png"))
    vision = Vision(cfg, source=lambda: (img, (0, 0, img.shape[1], img.shape[0])))
    vision.fixed_scale = SCALE  # some screenshots are cropped by a few pixels
    mouse = FakeMouse()
    return Bot(cfg, vision=vision, mouse=mouse, sleep=lambda s: None), vision, mouse


def target_of(bot, vision):
    rule, target = bot.evaluate(vision.capture())
    return rule, target


@pytest.mark.parametrize("screen", sorted({**START, **END}))
def test_every_screen_picks_the_right_thing(base, screen):
    bot, vision, _ = bot_on(base, screen)
    rule, target = target_of(bot, vision)
    expected = {**START, **END}[screen]
    got = rule.name if rule and rule.action in ("trainee", "card", "agenda") else target
    assert got == expected, f"{screen}: picked {target} ({rule.name if rule else None})"
    assert target not in ("reroll_sparks", "auto_select_dialog", "save_here")


def tap_point(bot, vision, mouse, screen):
    rule, target = target_of(bot, vision)
    bot.execute(rule, target, vision.capture())
    return mouse.clicks[-1] if mouse.clicks else None


def where(vision, name):
    m = vision.capture().find(name)
    return m.x, m.y, m.w, m.h


def inside(pt, box):
    x, y, w, h = box
    return abs(pt[0] - x) <= w / 2 and abs(pt[1] - y) <= h / 2


def test_keeps_a_preferred_trainee_and_presses_next(base):
    bot, vision, mouse = bot_on(base, "s03_trainee_oguri", trainees=["oguri_cap", "el_condor_pasa"])
    assert inside(tap_point(bot, vision, mouse, "s03"), where(vision, "next"))


def test_picks_the_preferred_trainee_when_another_is_selected(base):
    bot, vision, mouse = bot_on(base, "s03_trainee_oguri", trainees=["el_condor_pasa"])
    assert inside(tap_point(bot, vision, mouse, "s03"), where(vision, "trainee_el_condor_pasa_icon"))
    bot, vision, mouse = bot_on(base, "s15_trainee_condor", trainees=["oguri_cap"])
    assert inside(tap_point(bot, vision, mouse, "s15"), where(vision, "trainee_oguri_cap_icon"))


def test_automatic_legacies_leave_a_notice(base):
    bot, vision, mouse = bot_on(base, "s04_legacy_empty")
    tap_point(bot, vision, mouse, "s04")
    assert bot.notices and "automatically" in bot.notices[0]


def test_finds_light_hello_in_the_friend_list(base):
    cfg = config.load(base=base)
    assert tools.set_card(cfg, "https://gametora.com/umamusume/supports/30052-light-hello") == "Light Hello (SSR)"
    cfg = config.load(base=base)
    bot, vision, mouse = bot_on(base, "s08_borrow_list_light_hello")
    m = vision.find_card(vision.capture(), cfg["card"]["templates"], cfg["card"]["scales"], cfg["card"]["threshold"])
    assert m and 150 < m.x - 0 < 215 and 250 < m.y < 330  # the Light Hello row (window pixels)
    bot, vision, mouse = bot_on(base, "s07_borrow_list")  # not in this part of the list
    assert vision.find_card(vision.capture(), cfg["card"]["templates"], cfg["card"]["scales"],
                            cfg["card"]["threshold"]) is None


def test_training_focus_follows_the_setting(base):
    bot, vision, _ = bot_on(base, "s11_final_independent", focus="balanced")
    assert target_of(bot, vision)[1] == "focus_balanced_off"
    bot, vision, _ = bot_on(base, "s11_final_independent", focus="sprint")  # already Sprint
    assert target_of(bot, vision)[1] == "it_start"


def test_loads_the_saved_agenda_once_per_run(base):
    bot, vision, mouse = bot_on(base, "s11_final_independent", agenda="farmoguri")
    assert target_of(bot, vision)[1] == "agenda_edit"
    bot.execute(*target_of(bot, vision), vision.capture())
    assert target_of(bot, vision)[1] == "it_start"  # opened once this run: don't loop

    bot, vision, mouse = bot_on(base, "s12_agenda_empty", agenda="farmoguri")
    assert target_of(bot, vision)[1] == "my_agendas_button"

    bot, vision, mouse = bot_on(base, "s13_my_agendas", agenda="farmoguri")
    rule, target = target_of(bot, vision)
    assert rule.name == "Load the saved agenda"
    pt = tap_point(bot, vision, mouse, "s13")
    x, y, w, h = where(vision, "agenda_farmoguri")
    assert x + w / 2 < pt[0] < x + w / 2 + 120  # just right of the farmoguri box: its Load List
    assert y < pt[1] < y + h / 2 + 20           # lower half of the box (Save Here is the upper half)


def test_unknown_agenda_name_is_reported_not_guessed(base):
    bot, vision, _ = bot_on(base, "s13_my_agendas", agenda="does not exist")
    rule = next(r for r in bot.rules if r.name == "Load the saved agenda")
    assert not rule.enabled and "agenda_does_not_exist" in rule.missing


def test_every_screen_is_covered():
    names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(SCREENS, "*.png")))
    assert names == sorted({**START, **END})


def test_agenda_not_in_the_list_stops_instead_of_running_without_it(base):
    # an agenda the bot knows, but that isn't in this (static) list: it scrolls both ways, then stops
    other = cv2.imread(os.path.join(SCREENS, "s12_agenda_empty.png"))
    cv2.imwrite(os.path.join(base, "templates", "agenda_other.png"), cv2.resize(other[300:360, 100:330], None, fx=1 / SCALE, fy=1 / SCALE))
    bot, vision, mouse = bot_on(base, "s13_my_agendas", agenda="other")
    rule, target = target_of(bot, vision)
    assert rule.name == "Load the saved agenda"
    assert bot.execute(rule, target, vision.capture()) is None
    assert len(mouse.scrolls) == 2 and mouse.scrolls[0][2] < 0 < mouse.scrolls[1][2]  # down, then up
    assert bot.status == "Stopped: agenda not found" and mouse.clicks == []
