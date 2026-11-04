# Templates

Each template is a small PNG of a button, or of something that only appears on one screen. The rules in
[`config.toml`](../config.toml) refer to them by file name.

- All templates are stored at the scale of a **1920x1080 fullscreen** game. The bot rescales the screen for any
  other window size, so one set works everywhere.
- `templates/_downloaded/` holds fallback generic buttons. The app downloads them by itself and they are not committed.
- `templates/_card/` holds the support card chosen in the app (not committed).

## Screenshots needed

Templates are cropped from real screenshots of the game. Put them in [`screenshots/`](../screenshots/), numbered in
the order they happen. Use **PrtScn** in fullscreen, or **Alt+PrtScn** in windowed mode to capture just the game window.

| # | Screen | Templates cropped from it |
|---|---|---|
| 1 | Home screen | `home_career` |
| 2 | Every screen between pressing Career and the deck (scenario, trainee, legacy…) | `next` and anything else clicked |
| 3 | Deck screen with the **friend slot empty** | `empty_friend_slot` |
| 4 | Friend support card list | `friend_list`, `refresh` |
| 5 | Deck screen with the friend card chosen | `start_career` |
| 6 | Popup confirming the start (shows the TP cost) | `confirm_start` |
| 7 | TP recovery window: tap the **+** next to the TP bar on the home screen (don't use anything). Without it the bot still notices when TP runs out | `tp_popup`, `cancel` |
| 8 | Where Independent Training is turned on, plus its confirmation | `independent_training`, `confirm_independent_training` |
| 9 | Every screen from the end of the career back to Home, one per click | `complete_career`, `ok`, `close`, … |
| 10 | Any popup that would spend carats (don't confirm it) | `stop_carats` |
| 11 | When seen: veterans list full, connection error, title screen | `stop_veterans_full`, `connection_error` + `retry`, `title_screen` |

## Cropping by hand

```
python -m umabot crop home_career                                   # from the running game
python -m umabot crop home_career --from screenshots/01_home.png    # from a screenshot
```

Drag a rectangle around the button and press ENTER. Crop just the button with a small margin, avoiding numbers that
change (TP, fans, dates) and characters. The crop is converted to the reference scale automatically.
