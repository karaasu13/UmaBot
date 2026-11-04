"""Loads config.toml on top of the defaults."""
import copy
import os
import sys
import tomllib

DEFAULTS = {
    "game": {
        "window": "Umamusume",
        "reference_resolution": [1920, 1080],
        "threshold": 0.85,
        "thresholds": {},
    },
    "schedule": {
        "enabled": True,
        "start": "10:30",
        "end": "01:00",
        "jitter_min": 45,
        "breaks_per_day": [1, 3],
        "break_length_min": [15, 70],
        "finish_run_after_hours": True,
    },
    "human": {
        "reaction_s": [0.35, 1.6],
        "hold_ms": [55, 140],
        "distraction_chance": 0.04,
        "distraction_s": [6, 75],
        "poll_s": [0.8, 1.8],
    },
    "tp": {"wait_min": [25, 45]},
    "card": {
        "templates": ["card_light_hello", "card_light_hello_full"],
        "threshold": 0.75,
        "scales": [0.4, 1.8],
        "scroll_pos": [0.30, 0.55],
        "scroll_amount": -5,
        "scroll_attempts": 6,
        "refresh": "refresh",
        "max_failures": 8,
    },
    "safety": {
        "max_repeats": 25,
        "unknown_screen_min": 40,
        "discord_webhook": "",
    },
    "rule": [],
}


def base_dir():
    """Project folder (or the .exe's folder in the compiled version)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _merge(base, new):
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None, base=None):
    base = base or base_dir()
    path = path or os.path.join(base, "config.toml")
    data = {}
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = tomllib.load(f)
    cfg = _merge(copy.deepcopy(DEFAULTS), data)
    cfg["_base"] = base
    return cfg
