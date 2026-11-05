"""Configuration: defaults <- config.toml (advanced settings) <- settings.json (choices made in the menu)."""
import copy
import json
import os
import sys
import tomllib

SETTINGS_FILE = "settings.json"

DEFAULTS = {
    "game": {
        "window": "Umamusume",
        "reference_resolution": [1920, 1080],
        "scale": "auto",
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
    "training": {
        "trainees": ["oguri_cap", "el_condor_pasa"],  # preferred trainees, in order
        "focus": "sprint",                           # balanced | stamina | sprint
        "agenda": "",                                # name of a saved agenda in My Agendas ("" = keep the game's)
    },
    "tp": {"wait_min": [25, 45], "guess_after": 3, "guess_window_min": 10},
    "card": {
        "id": 0,            # GameTora ID of the support card to borrow (0 = not chosen yet)
        "name": "",
        "templates": ["card", "card_full"],
        "threshold": 0.75,
        "scales": [0.4, 1.8],
        "scroll_pos": "auto",
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


def cli_name():
    """How the user invokes the bot, for help messages."""
    return "UmaBot.exe" if getattr(sys, "frozen", False) else "python -m umabot"


def _merge(base, new):
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_settings(base):
    try:
        with open(os.path.join(base, SETTINGS_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(base, changes):
    data = _merge(load_settings(base), changes)
    with open(os.path.join(base, SETTINGS_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def load(path=None, base=None):
    base = base or base_dir()
    path = path or os.path.join(base, "config.toml")
    data = {}
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = tomllib.load(f)
    cfg = _merge(copy.deepcopy(DEFAULTS), data)
    _merge(cfg, load_settings(base))
    cfg["_base"] = base
    return cfg
