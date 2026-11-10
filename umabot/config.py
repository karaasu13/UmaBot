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
        "reaction_s": [0.2, 0.8],
        "hold_ms": [55, 140],
        "distraction_chance": 0.02,
        "distraction_s": [4, 30],
        "poll_s": [0.4, 0.9],
    },
    "training": {
        "trainees": ["oguri_cap", "el_condor_pasa"],  # preferred trainees, in order
        "focus": "sprint",                           # balanced | stamina | sprint
        "agenda": "",                                # name of a saved agenda in My Agendas ("" = keep the game's)
        "veterans_free": -1,                         # free spaces in the veterans list (-1 = don't track)
        "event_boost": True,                         # tick "Event Boost (TP Usage x2)" when the game has one
    },
    "tp": {"wait_min": [25, 45], "guess_after": 3, "guess_window_min": 10, "run_cost": 30, "regen_min": 10,
           "ask_toughness": True, "ask_minutes": 10},
    "card": {
        "id": 0,            # GameTora ID of the support card to borrow (0 = not chosen yet)
        "name": "",
        "templates": ["card", "card_full"],
        "threshold": 0.75,
        "scales": [0.4, 1.8],
        "scroll_pos": "auto",
        "scroll_amount": -8,
        "scroll_attempts": 20,
        "refresh": "refresh",
        "max_failures": 8,
    },
    "safety": {
        "max_repeats": 25,
        "unknown_screen_min": 10,   # moving, but its words not changing, for this long: alert, then restart
        "read_screen": True,        # read the words on unknown screens and press safe buttons
        "read_after_s": 8,
        "stuck_alert_min": 2,       # the same unknown screen, not moving, for this long: alert
        "restart_game": True,       # close and reopen the game when really stuck (or open it if it's gone)
        "restart_after_min": 20,
        "reopen_after_min": 5,
        "steam_app_id": 3224770,
        "discord_webhook": "",
        "discord_each_run": False,
        "discord_bot_token": "",    # optional: a Discord bot of your own, to answer UmaBot from Discord
        "discord_channel": "",
        "stop_on_user_input": True,
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
