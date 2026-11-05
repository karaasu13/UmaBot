# Templates

Each template is a small PNG of a button, or of something that only appears on one screen. The rules in
[`config.toml`](../config.toml) refer to them by file name.

- All templates are stored at the scale of a **1920x1080 fullscreen** game. The bot rescales the screen for any
  other window size, so one set works everywhere.
- `templates/_downloaded/` holds fallback generic buttons. The app downloads them by itself and they are not committed.
- `templates/_card/` holds the support card chosen in the app (not committed).

## Screenshots

Templates are cropped from real screenshots of the game (window 1106x622) and every screen is covered by
`tests/test_real_screens.py`. Done: the whole run, from the Career button to To Home, including trainee choice,
automatic legacies, the friend card, Independent Training with its focus, loading a saved agenda, and Recover TP.

Still missing (only appear sometimes):

| Screen | Template |
|---|---|
| Any popup that would spend carats (don't confirm it) | `stop_carats` |
| Veterans list full | `stop_veterans_full` |
| Connection error and its Retry button | `connection_error`, `retry` |
| Title screen ("Tap to start") | `title_screen` |

New trainees: a screenshot of Trainee Select with her selected gives `trainee_<name>_banner` (the name banner) and
`trainee_<name>_icon` (her face in the grid). Saved agendas are taught from the app ("Show it to UmaBot").

## Cropping by hand

```
python -m umabot crop home_career                                   # from the running game
python -m umabot crop home_career --from screenshots/01_home.png    # from a screenshot
```

Drag a rectangle around the button and press ENTER. Crop just the button with a small margin, avoiding numbers that
change (TP, fans, dates) and characters. The crop is converted to the reference scale automatically.
