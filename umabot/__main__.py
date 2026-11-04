"""python -m umabot [command]"""
import argparse
import sys

from . import config, tools
from .logger import setup
from .vision import enable_dpi_awareness


def main(argv=None):
    p = argparse.ArgumentParser(prog="umabot", description="Fan-farming bot for Umamusume (Steam).")
    p.add_argument("--config", help="path to config.toml")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the bot (default when no command is given)")
    sub.add_parser("download", help="download the generic buttons and the Light Hello card")
    sub.add_parser("check", help="list the templates that are still missing (no game needed)")
    sub.add_parser("test", help="screenshot the game and show what the bot sees and would do")
    c = sub.add_parser("crop", help="crop a template from the game or from a screenshot")
    c.add_argument("name")
    c.add_argument("--from", dest="file", help="crop from a PNG file instead of the game")
    sub.add_parser("position", help="show the mouse position relative to the game")
    sub.add_parser("schedule", help="show the schedule for the next few days")
    a = p.parse_args(argv)

    enable_dpi_awareness()
    cfg = config.load(a.config)
    cmd = a.cmd or "run"
    if cmd == "download":
        tools.download(cfg)
    elif cmd == "check":
        tools.check(cfg)
    elif cmd == "test":
        tools.test(cfg)
    elif cmd == "crop":
        tools.crop(cfg, a.name, a.file)
    elif cmd == "position":
        tools.position(cfg)
    elif cmd == "schedule":
        tools.schedule(cfg)
    else:
        from .engine import Bot
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
