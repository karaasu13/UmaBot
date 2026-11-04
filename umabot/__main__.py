"""UmaBot. With no command it opens the app; the commands are for running it from a terminal."""
import argparse
import sys

from . import config, tools
from .logger import setup
from .vision import enable_dpi_awareness


def main(argv=None):
    p = argparse.ArgumentParser(prog="umabot", description="Hands-free fan farming for Umamusume (Steam). "
                                "Run it without a command to open the app.")
    p.add_argument("--config", help="path to config.toml")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the bot in the terminal, without the app")
    c = sub.add_parser("card", help="choose the support card to borrow (GameTora link, card ID or .png)")
    c.add_argument("value", nargs="?")
    sub.add_parser("test", help="show what the bot sees right now and save a screenshot to logs/")
    sub.add_parser("schedule", help="show the schedule for the next few days")
    sub.add_parser("check", help="(advanced) list rules and missing templates")
    sub.add_parser("download", help="(advanced) download the fallback generic buttons")
    cr = sub.add_parser("crop", help="(advanced) crop a template from the game or from a screenshot")
    cr.add_argument("name")
    cr.add_argument("--from", dest="file", help="crop from a PNG file instead of the game")
    sub.add_parser("position", help="(advanced) show the mouse position relative to the game")
    sub.add_parser("selftest", help=argparse.SUPPRESS)
    a = p.parse_args(sys.argv[1:] if argv is None else argv)

    enable_dpi_awareness()
    if a.cmd is None:
        from . import gui
        return gui.run(a.config)
    if a.cmd == "selftest":
        from . import gui
        return gui.selftest(a.config)

    cfg = config.load(a.config)
    if a.cmd == "card":
        if a.value:
            print(f"Support card set: {tools.set_card(cfg, a.value)}")
        else:
            tools.choose_card(cfg)
    elif a.cmd == "test":
        tools.test(cfg)
    elif a.cmd == "schedule":
        tools.schedule(cfg)
    elif a.cmd == "check":
        tools.check(cfg)
    elif a.cmd == "download":
        tools.download(cfg)
    elif a.cmd == "crop":
        tools.crop(cfg, a.name, a.file)
    elif a.cmd == "position":
        tools.position(cfg)
    elif a.cmd == "run":
        run_in_terminal(cfg)


def run_in_terminal(cfg):
    from .engine import Bot
    if tools.missing_fallback(cfg):
        tools.download(cfg, quiet=True, only_missing=True)
    if not tools.has_card(cfg):
        tools.choose_card(cfg)
    setup(cfg["_base"])
    try:
        Bot(cfg).run()
    except KeyboardInterrupt:
        print("\nstopped.")
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            print("\nstopped (mouse in the screen corner).")
        else:
            raise


if __name__ == "__main__":
    sys.exit(main())
