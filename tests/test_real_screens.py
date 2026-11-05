"""The end of a real career (screenshots from the Steam game, window 1106x622): on every screen the bot
must pick the right button, and never anything that spends TP (Reroll Sparks)."""
import glob
import os
import shutil

import cv2
import pytest

from umabot import config
from umabot.engine import Bot
from umabot.vision import Vision

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(REPO, "tests", "screens")

EXPECTED = {
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


class NullMouse:
    def __getattr__(self, _):
        return lambda *a, **k: None


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    base = tmp_path_factory.mktemp("bot")  # only the repo's own templates and rules
    shutil.copytree(os.path.join(REPO, "templates"), base / "templates")
    shutil.copy(os.path.join(REPO, "config.toml"), base / "config.toml")
    return config.load(base=str(base))


@pytest.mark.parametrize("screen", sorted(EXPECTED))
def test_end_of_career_screens(cfg, screen):
    img = cv2.imread(os.path.join(SCREENS, screen + ".png"))
    vision = Vision(cfg, source=lambda: (img, (0, 0, img.shape[1], img.shape[0])))
    vision.fixed_scale = 622 / 1080  # some screenshots are cropped by a few pixels
    bot = Bot(cfg, vision=vision, mouse=NullMouse(), sleep=lambda s: None)
    rule, target = bot.evaluate(vision.capture())
    assert target == EXPECTED[screen], f"{screen}: picked {target} ({rule.name if rule else None})"
    assert "reroll" not in str(target)


def test_scale_is_measured_on_a_real_screen(cfg):
    img = cv2.imread(os.path.join(SCREENS, "13_career_rank.png"))  # full 1106x622 window
    vision = Vision(cfg, source=lambda: (img, (0, 0, img.shape[1], img.shape[0])))
    assert vision.capture().find("next")
    assert vision.scale == pytest.approx(622 / 1080, rel=0.02)


def test_every_screen_is_covered():
    assert sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(SCREENS, "*.png"))) == sorted(EXPECTED)
