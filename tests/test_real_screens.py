"""A whole real run (screenshots from the Steam game, window 1106x622): on every screen the bot must pick
the right button, and never anything that spends TP or carats."""
import glob
import os
import shutil

import cv2
import numpy as np
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
    "s17_tp_not_enough": "no",  # never Restore
    "s18_final_event_boost": "event_boost_off",  # ticked before Start (option on by default)
    "s19_recover_tp_event": "close",
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


def screen_vision(base, path):
    img = cv2.imread(path if os.path.isabs(path) else os.path.join(SCREENS, path + ".png"))
    vision = Vision(config.load(base=base), source=lambda: (img, (0, 0, img.shape[1], img.shape[0])))
    vision.fixed_scale = SCALE
    return vision


AGENDAS = os.path.join(SCREENS, "agendas")
# the user's own My Agendas lists: (screen, a box like the user drags, where its Load List is)
TAUGHT = {
    "farmoguri": (os.path.join(AGENDAS, "farmoguri.png"), (160, 200, 180, 85), 286),
    "seiunsolv": (os.path.join(AGENDAS, "farmoguri.png"), (160, 308, 330, 95), 393),   # box covers the buttons
    "longahh": (os.path.join(AGENDAS, "farmoguri.png"), (160, 430, 175, 50), 500),     # races only
    "milemeda": (os.path.join(AGENDAS, "milemed.png"), (160, 215, 180, 85), 287),
    "milemed": (os.path.join(AGENDAS, "milemed.png"), (160, 322, 180, 85), 394),
    "sprintmed": (os.path.join(AGENDAS, "milemed.png"), (160, 430, 180, 85), 501),
}


def teach(base, agenda):
    path, box, _ = TAUGHT[agenda]
    cfg = config.load(base=base)
    frame = screen_vision(base, path).capture()
    assert tools.check_agenda(cfg, frame, box) is None
    tools.save_agenda(cfg, agenda, frame, box)


def bot_on(base, screen, **training):
    cfg = config.load(base=base)
    cfg["training"].update(training)
    img = cv2.imread(screen if os.path.isabs(screen) else os.path.join(SCREENS, screen + ".png"))
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
    teach(base, "farmoguri")
    bot, vision, mouse = bot_on(base, "s11_final_independent", agenda="farmoguri")
    for _ in range(3):  # back on Final Confirmation without the agenda: it opens it again, 3 times at most
        assert target_of(bot, vision)[1] == "agenda_edit"
        bot.execute(*target_of(bot, vision), vision.capture())
    assert target_of(bot, vision)[0] is None  # and never starts a run without it
    bot.agenda_loaded = True
    assert target_of(bot, vision)[1] == "it_start"

    bot, vision, mouse = bot_on(base, "s12_agenda_empty", agenda="farmoguri")
    assert target_of(bot, vision)[1] == "my_agendas_button"

    bot, vision, mouse = bot_on(base, "s13_my_agendas", agenda="farmoguri")
    rule, target = target_of(bot, vision)
    assert rule.name == "Load the saved agenda"
    bot.execute(rule, target, vision.capture())
    assert len(mouse.clicks) == 1 and abs(mouse.clicks[0][1] - 368) < 8  # farmoguri's Load List


def test_unknown_agenda_name_is_reported_not_guessed(base):
    pytest.importorskip("rapidocr_onnxruntime")
    bot, vision, mouse = bot_on(base, "s13_my_agendas", agenda="does not exist")
    assert bot.load_agenda() is None and mouse.clicks == []
    assert len(mouse.scrolls) == 2 and "same as in the game" in bot.notices[-1]


@pytest.mark.parametrize("screen,typed,load_y", [
    ("agendas/farmoguri", "farmoguri", 286), ("agendas/farmoguri", "seiunsolv", 393),
    ("agendas/farmoguri", "LongAhh", 500), ("agendas/milemed", "MileMed+A", 287),
    ("agendas/milemed", "milemed", 394), ("agendas/milemed", "Sprint Med", 501),
    ("s13_my_agendas", "farmoguri", 368), ("s13_my_agendas", "seiunsolv", 475),
])
def test_agenda_is_found_by_its_name_without_showing_it(base, screen, typed, load_y):
    # UmaBot reads the names in My Agendas: typing the name is enough (case and spaces don't matter),
    # and "MileMed" is never mistaken for "MileMed +A"
    pytest.importorskip("rapidocr_onnxruntime")
    bot, vision, mouse = bot_on(base, screen, agenda=typed)
    bot.load_agenda()
    assert len(mouse.clicks) == 1 and abs(mouse.clicks[0][1] - load_y) < 8


def test_every_screen_is_covered():
    names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(SCREENS, "*.png")))
    assert names == sorted({**START, **END})


def test_agenda_not_in_the_list_stops_instead_of_running_without_it(base):
    # an agenda the bot knows, but that isn't in this (static) list: it scrolls both ways, then stops
    teach(base, "longahh")
    bot, vision, mouse = bot_on(base, "s13_my_agendas", agenda="longahh")
    rule, target = target_of(bot, vision)
    assert rule.name == "Load the saved agenda"
    assert bot.execute(rule, target, vision.capture()) is None
    assert len(mouse.scrolls) == 2 and mouse.scrolls[0][2] < 0 < mouse.scrolls[1][2]  # down, then up
    assert bot.status == "Stopped: agenda not found" and mouse.clicks == []


def test_an_old_picture_of_the_agenda_doesnt_get_in_the_way(base):
    pytest.importorskip("rapidocr_onnxruntime")
    cv2.imwrite(os.path.join(base, "templates", "agenda_farmoguri.png"), np.full((60, 200, 3), 128, np.uint8))
    bot, vision, mouse = bot_on(base, "s13_my_agendas", agenda="farmoguri")
    bot.load_agenda()
    assert len(mouse.clicks) == 1 and abs(mouse.clicks[0][1] - 368) < 8


def test_agenda_crops_never_set_the_ui_scale(base):
    # a taught agenda can be a bad crop (once it was a picture of UmaBot itself): it must not measure the scale
    teach(base, "farmoguri")
    tdir = os.path.join(base, "templates")
    for f in os.listdir(tdir):
        if f.endswith(".png") and f != "agenda_farmoguri.png":
            os.remove(os.path.join(tdir, f))
    cfg = config.load(base=base)
    img = cv2.imread(os.path.join(SCREENS, "s13_my_agendas.png"))
    vision = Vision(cfg, source=lambda: (img, (0, 0, img.shape[1], img.shape[0])))
    vision.templates = {n: t for n, t in vision.templates.items() if n == "agenda_farmoguri"}
    frame = vision.capture()
    assert not vision.maybe_calibrate(frame, force=True) and not vision.calibrated


def test_old_calibration_files_are_measured_again(base):
    import json
    with open(os.path.join(base, "calibration.json"), "w") as f:
        json.dump({"1106x622": 0.541}, f)  # older version: maybe measured on an agenda crop
    assert Vision(config.load(base=base))._calibration == {}
    with open(os.path.join(base, "calibration.json"), "w") as f:
        json.dump({"_v": 2, "1106x622": 0.576}, f)
    assert Vision(config.load(base=base))._calibration == {"1106x622": 0.576}


@pytest.mark.parametrize("agenda", sorted(TAUGHT))
def test_taught_agenda_is_loaded_only_where_it_is(base, agenda):
    # several agendas share races (Niigata + Arima Kinen) or names ("MileMed" / "MileMed+A"): never the wrong one
    teach(base, agenda)
    screens = {os.path.join(AGENDAS, "farmoguri.png"): {"farmoguri": 286, "seiunsolv": 393, "longahh": 500},
               os.path.join(AGENDAS, "milemed.png"): {"milemeda": 287, "milemed": 394, "sprintmed": 501},
               os.path.join(SCREENS, "s13_my_agendas.png"): {"farmoguri": 368, "seiunsolv": 475}}
    for screen, here in screens.items():
        bot, vision, mouse = bot_on(base, screen, agenda=agenda)
        bot.load_agenda()
        if agenda in here:
            assert len(mouse.clicks) == 1 and abs(mouse.clicks[0][1] - here[agenda]) < 8, screen
        else:
            assert mouse.clicks == [], screen


def test_crop_check_refuses_bad_boxes(base):
    cfg = config.load(base=base)
    frame = screen_vision(base, "s13_my_agendas").capture()
    assert "bigger" in tools.check_agenda(cfg, frame, (160, 300, 300, 12))   # just a thin strip of the name
    assert tools.check_agenda(cfg, frame, (700, 100, 200, 100))              # not an agenda at all


def test_not_enough_tp_says_no_and_waits(base):
    bot, vision, mouse = bot_on(base, "s17_tp_not_enough")
    rule, target = target_of(bot, vision)
    assert rule.name.startswith("Out of TP: say No") and target == "no"
    wait = bot.execute(rule, target, vision.capture())
    assert wait >= 60 and len(mouse.clicks) == 1
    x, y = mouse.clicks[0]
    assert x < 320 and 380 < y < 430  # the No button, left; Restore is on the right


@pytest.fixture(scope="module")
def reader():
    pytest.importorskip("rapidocr_onnxruntime")
    from umabot.reader import Reader
    return Reader()


@pytest.mark.parametrize("screen,label", [
    ("10_training_log", "OK"), ("12_finish_dialog", "Finish"), ("13_career_rank", "Next"),
    ("16_details", "Close"), ("18_rewards_fans", "Next"), ("20_career_complete", "To Home"),
    ("s05_autoselect_dialog", "OK"), ("s16_recover_tp", "Close"), ("s17_tp_not_enough", "No"),
    ("s01_home", None),
])
def test_reading_real_screens_picks_the_right_button(reader, screen, label):
    # what UmaBot would do on these screens if an event changed their look and no picture matched
    from umabot.reader import decide
    choice = decide(reader.read(cv2.imread(os.path.join(SCREENS, screen + ".png"))))
    assert (choice[0] if choice else None) == label


# ---------------------------------------------------------------- event, TP and Toughness (real screens, real reading)
@pytest.fixture()
def ocr():
    pytest.importorskip("rapidocr_onnxruntime")


def test_event_boost_is_ticked(base, ocr):
    bot, vision, mouse = bot_on(base, "s18_final_event_boost", agenda="farmoguri")
    rule, target = target_of(bot, vision)
    assert rule.name == "Open the agenda"  # the agenda first
    bot.agenda_loaded = True
    rule, target = target_of(bot, vision)
    assert rule.name == "Event Boost"
    bot.execute(rule, target, vision.capture())
    x, y = mouse.clicks[0]
    assert 168 < x < 300 and 458 < y < 480  # the box left of "Event Boost (TP Usage x2)"
    rule, target = target_of(bot, vision)
    assert target == "it_start"  # once per run, then Start


def test_final_confirmation_by_its_words_when_no_picture_matches(base, ocr):
    bot, vision, mouse = bot_on(base, "s18_final_event_boost", agenda="farmoguri")
    frame = vision.capture()
    words = bot.recovery.reader.read(frame.raw)
    from umabot.reader import decide
    assert decide(words) is None  # never Cancel here
    assert bot.by_text(frame, words, can_start=True) is not None
    assert abs(mouse.clicks[-1][0] - 450) < 40 and abs(mouse.clicks[-1][1] - 299) < 15  # Edit
    bot.agenda_loaded = True
    bot.by_text(frame, words, can_start=True)  # the Event Boost
    assert abs(mouse.clicks[-1][1] - 468) < 15
    assert bot.by_text(frame, words, can_start=False) is None  # off hours: nothing (and no Cancel)
    bot.by_text(frame, words, can_start=True)
    assert abs(mouse.clicks[-1][1] - 574) < 15 and mouse.clicks[-1][0] > 330 and bot.runs_today == 1  # Start!


class Remote:
    """What the bot uses of the Discord bot, for these tests."""
    def __init__(self):
        from umabot.remote import Remote as Real
        self.real = Real(discord=None)
        self.posts = []
        self.real.say = lambda text, image=None: self.posts.append((text, image))


def test_out_of_tp_asks_for_a_toughness_and_uses_it_when_allowed(base, ocr):
    r = Remote()
    bot, vision, mouse = bot_on(base, "s17_tp_not_enough")
    bot.remote = r.real
    rule, target = target_of(bot, vision)
    assert bot.execute(rule, target, vision.capture()) == 15 and mouse.clicks == []  # waits for the answer
    assert "Toughness" in r.posts[0][0] and "9 more" in r.posts[0][0] and r.posts[0][1][:4] == b"\x89PNG"
    r.real.commands.put("sim")
    r.real.handle(bot, bot.now())
    bot.execute(rule, target, vision.capture())
    assert mouse.clicks[-1][0] > 330  # Restore (right), allowed
    # the Recover TP list: Use on the Toughness 30 row, never the carats
    bot2, vision2, mouse2 = bot_on(base, "s19_recover_tp_event")
    bot2.remote, bot2.tp_approval = r.real, bot.tp_approval
    rule, target = target_of(bot2, vision2)
    bot2.execute(rule, target, vision2.capture())
    x, y = mouse2.clicks[-1]
    assert 420 < x < 460 and 145 < y < 170  # "Use" of Toughness 30 (the carats' Use is above it)
    from umabot.reader import Word, decide_approved
    confirm = [Word("Use Toughness 30?", (0, 0, 200, 20)), Word("Cancel", (0, 90, 60, 20)), Word("OK", (200, 90, 40, 20))]
    assert decide_approved(confirm, "toughness")[0] == "OK"
    assert decide_approved([Word("Carats", (0, 0, 60, 20)), Word("Toughness", (0, 30, 60, 20)),
                            Word("Use", (0, 90, 40, 20))], "toughness") is None


def test_out_of_tp_without_an_answer_says_no_and_waits_what_the_game_says(base, ocr, monkeypatch):
    sent = []
    monkeypatch.setattr("umabot.engine.notify", lambda hook, msg, image=None: sent.append((msg, image)))
    bot, vision, mouse = bot_on(base, "s17_tp_not_enough")  # no Discord bot: no question
    rule, target = target_of(bot, vision)
    wait = bot.execute(rule, target, vision.capture())
    assert mouse.clicks[-1][0] < 320  # No
    assert 90 * 60 < wait < 100 * 60  # 9 TP missing x 10 min
    assert sent and "waiting until" in sent[-1][0] and sent[-1][1][:4] == b"\x89PNG"


def test_out_of_tp_is_checked_on_screen_before_waiting(base, ocr, monkeypatch):
    sent = []
    monkeypatch.setattr("umabot.engine.notify", lambda hook, msg, image=None: sent.append(msg))
    bot, vision, mouse = bot_on(base, "s01_home")  # TP 100/100 on screen
    assert bot._tp_guess() == 2.0 and "Start career" in bot.blocked and "100 TP" in sent[-1]
