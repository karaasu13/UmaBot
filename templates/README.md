# Templates

Each template is a PNG crop of a button (or of something that identifies a screen).
The file name must be **exactly** the one in the tables below, because that's the name the rules in `config.toml` use.

- `templates/_downloaded/` is created by `python -m umabot download` and is not committed to git.
- `templates/*.png` are your own crops. If one has the same name as a downloaded one, yours wins.

Always crop with the game in **fullscreen at 1920x1080**. Other sizes with the same aspect ratio
(1600x900, 1280x720, ...) also work because the bot rescales, but 1920x1080 is the most reliable.

## Already provided by `download`

| Name | What it is |
|---|---|
| `next`, `next2` | "Next" button |
| `ok` | "OK" button |
| `close` | "Close" button |
| `cancel` | "Cancel" button (used on the TP popup) |
| `skip`, `skip_big` | ⏭ skip button |
| `complete_career` | pink "Complete Career" button |
| `confirm`, `back`, `ok2`, `career_lobby` | extras, not used by any rule yet |
| `card_light_hello`, `card_light_hello_full` | icon of the SSR Light Hello [From the Ground Up] card, from GameTora |

> The buttons come from the [umamusume-auto-train](https://github.com/samsulpanjul/umamusume-auto-train) project
> (Steam version, 1920x1080) and the card from [GameTora](https://gametora.com/umamusume/supports/30052-light-hello).
> They are downloaded to your PC and are not stored in this repository.

## Still to crop (needs the game)

| Name | Where it shows up | What for |
|---|---|---|
| `home_career` | home screen, **Career** button | start a run (only inside the schedule) |
| `empty_friend_slot` | deck screen, the empty friend card slot | open the friend list |
| `friend_list` | friend card list: something only that screen has (e.g. its title) | know when to look for Light Hello |
| `refresh` | friend list, refresh button (optional) | if Light Hello isn't listed |
| `start_career` | deck screen, **Start Career!** button | |
| `confirm_start` | popup confirming the start (the one showing the 30 TP) | counts runs |
| `independent_training` | button that turns on Independent Training | |
| `confirm_independent_training` | Independent Training confirmation (if there is one) | |
| `tp_popup` | not-enough-TP popup: something only it has (e.g. its title) | cancel and wait |
| `stop_carats` | any popup that would **spend carats** | **stops the bot**: very important |
| `stop_veterans_full` | veterans list full warning | stops the bot |
| `title_screen` | title screen ("Tap to start") | clicks the middle |
| `connection_error` + `retry` | connection error popup and its **Retry** button | |

### How to crop

```
python -m umabot crop home_career
```
It takes a screenshot of the game 3 seconds later. Drag a rectangle around the button and press ENTER.

Crop just the button, with a small margin. Avoid numbers that change (TP, fans, dates) and the character.

### Alternative: send full screenshots

If that's easier, take full-screen screenshots (PrtScn key, game at 1920x1080) of each screen in the table,
put them in `screenshots/` named after the template (e.g. `screenshots/home_career.png`) and push.
The crops can then be made from those, or you can do it yourself with:

```
python -m umabot crop home_career --from screenshots/home_career.png
```
