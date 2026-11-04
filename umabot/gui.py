"""UmaBot desktop app: choose the support card, set the schedule, start/stop and watch what it does."""
import contextlib
import io
import logging
import os
import queue
import re
import sys
import threading
import traceback
import webbrowser
from datetime import date, datetime, timedelta
from datetime import time as dtime

import customtkinter as ctk
from PIL import Image

from . import __version__, config, tools
from .engine import Bot, prepare_rules
from .logger import log, setup as setup_logging
from .schedule import Schedule
from .vision import load_templates

# Palette taken from the app icon
BG = "#0b1324"
PANEL = "#121c31"
BORDER = "#1e2a45"
FIELD = "#0d1628"
TEXT = "#e6edf8"
MUTED = "#8a9cbc"
CORAL = "#e38081"
CORAL_HOVER = "#cc6b6d"
BLUE = "#4872b8"
BLUE_LIGHT = "#b4cbef"
GREEN = "#5cc98b"
AMBER = "#e8b85c"
RED = "#e06767"
KIND_COLORS = {"ok": GREEN, "wait": AMBER, "stop": MUTED, "error": RED}
GAMETORA_SUPPORTS = "https://gametora.com/umamusume/supports"
TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")


def resource(rel):
    """Files bundled with the app (inside the .exe, or in the repo when running from source)."""
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, rel)


class _ToQueue(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.q.put(self.format(record))


class App(ctk.CTk):
    def __init__(self, cfg_path=None):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.cfg_path = cfg_path
        self.cfg = config.load(cfg_path)
        self.base = self.cfg["_base"]
        self.bot, self.thread = None, None
        self.stop_event = threading.Event()
        self.logq, self.uiq = queue.Queue(), queue.Queue()

        setup_logging(self.base)
        self._handler = _ToQueue(self.logq)
        log.addHandler(self._handler)

        families = set(self.tk.call("font", "families"))
        self.family = "Segoe UI" if sys.platform == "win32" else ("Inter" if "Inter" in families else None)

        self.title("UmaBot")
        self.geometry("960x600")
        self.minsize(880, 560)
        self._set_icon()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_card()
        self._refresh_schedule_preview()
        self._refresh_screens()
        if tools.missing_fallback(self.cfg):
            self._in_background(self._fetch_fallback_buttons)
        if tools.has_card(self.cfg):
            log.info("UmaBot is ready. Open the game and press Start.")
        else:
            log.info("Welcome! Paste the GameTora link of the support card to borrow, then press Start.")
        self.after(300, self._poll)

    # ------------------------------------------------------------ building blocks
    def font(self, size=13, weight="normal"):
        return ctk.CTkFont(family=self.family, size=size, weight=weight)

    def _set_icon(self):
        try:
            if sys.platform == "win32":
                ico = resource(os.path.join("assets", "icon.ico"))
                self.iconbitmap(ico)
                self.after(250, lambda: self.iconbitmap(ico))  # customtkinter resets it on start
            else:
                import tkinter
                self._icon_photo = tkinter.PhotoImage(file=resource(os.path.join("assets", "icon.png")))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _panel(self, master, title, row, expand=False):
        frame = ctk.CTkFrame(master, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=14)
        frame.grid(row=row, column=0, sticky="nsew", pady=(0, 12))
        frame.grid_columnconfigure(1, weight=1)
        if expand:
            master.grid_rowconfigure(row, weight=1)
        ctk.CTkLabel(frame, text=title.upper(), font=self.font(11, "bold"), text_color=MUTED).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(12, 4))
        return frame

    def _button(self, master, text, command, primary=False, **kw):
        style = dict(fg_color=CORAL, hover_color=CORAL_HOVER, text_color="#1a0f12") if primary else dict(
            fg_color="transparent", hover_color=BORDER, border_width=1, border_color=BORDER, text_color=TEXT)
        style.update(kw)
        return ctk.CTkButton(master, text=text, command=command, corner_radius=10,
                             font=self.font(14 if primary else 13, "bold" if primary else "normal"), **style)

    def _build(self):
        self.grid_columnconfigure(0, weight=0, minsize=440)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 14))
        head.grid_columnconfigure(1, weight=1)
        try:
            logo = Image.open(resource(os.path.join("assets", "icon.png")))
            self._logo = ctk.CTkImage(light_image=logo, dark_image=logo, size=(52, 52))
            ctk.CTkLabel(head, image=self._logo, text="").grid(row=0, column=0, rowspan=2, padx=(0, 12))
        except Exception:
            pass
        ctk.CTkLabel(head, text="UmaBot", font=self.font(24, "bold"), text_color=TEXT).grid(
            row=0, column=1, sticky="sw")
        ctk.CTkLabel(head, text="Hands-free fan farming for Umamusume", font=self.font(13),
                     text_color=MUTED).grid(row=1, column=1, sticky="nw")
        self.pill = ctk.CTkLabel(head, text="●  Stopped", font=self.font(13, "bold"), text_color=MUTED,
                                 fg_color=PANEL, corner_radius=14, height=32, padx=14)
        self.pill.grid(row=0, column=2, rowspan=2, sticky="e")

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(20, 6))
        left.grid_columnconfigure(0, weight=1)
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 20))
        right.grid_columnconfigure(0, weight=1)

        # support card
        card = self._panel(left, "Support card", 0)
        self.card_img = ctk.CTkLabel(card, text="?", width=66, height=88, fg_color=FIELD, corner_radius=8,
                                     font=self.font(28, "bold"), text_color=BORDER)
        self.card_img.grid(row=1, column=0, rowspan=3, padx=(16, 14), pady=(2, 14), sticky="n")
        self.card_name = ctk.CTkLabel(card, text="", font=self.font(16, "bold"), text_color=TEXT, anchor="w")
        self.card_name.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 16))
        self.card_msg = ctk.CTkLabel(card, text="", font=self.font(12), text_color=MUTED, anchor="w",
                                     justify="left", wraplength=300)
        self.card_msg.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 16))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=(8, 14))
        row.grid_columnconfigure(0, weight=1)
        self.card_entry = ctk.CTkEntry(row, placeholder_text="GameTora link or card ID", height=34,
                                       fg_color=FIELD, border_color=BORDER, text_color=TEXT, font=self.font(13))
        self.card_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.card_entry.bind("<Return>", lambda e: self._set_card())
        self.card_btn = self._button(row, "Set card", self._set_card, width=90, height=34)
        self.card_btn.grid(row=0, column=1)
        browse = ctk.CTkLabel(row, text="Browse support cards on GameTora  ↗", font=self.font(12),
                              text_color=BLUE_LIGHT, cursor="hand2")
        browse.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        browse.bind("<Button-1>", lambda e: webbrowser.open(GAMETORA_SUPPORTS))

        # schedule
        sch = self._panel(left, "Schedule", 1, expand=True)
        s = self.cfg["schedule"]
        self.sched_on = ctk.BooleanVar(value=bool(s["enabled"]))
        self.sched_start = ctk.StringVar(value=s["start"])
        self.sched_end = ctk.StringVar(value=s["end"])
        ctk.CTkSwitch(sch, text="Only play at normal hours", variable=self.sched_on, font=self.font(13),
                      text_color=TEXT, progress_color=CORAL, command=self._schedule_changed).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=16)
        times = ctk.CTkFrame(sch, fg_color="transparent")
        times.grid(row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 2))
        for i, (label, var) in enumerate((("From", self.sched_start), ("to", self.sched_end))):
            ctk.CTkLabel(times, text=label, font=self.font(13), text_color=MUTED).grid(
                row=0, column=i * 2, padx=(0 if i == 0 else 10, 8))
            e = ctk.CTkEntry(times, textvariable=var, width=74, height=32, justify="center", fg_color=FIELD,
                             border_color=BORDER, text_color=TEXT, font=self.font(13))
            e.grid(row=0, column=i * 2 + 1)
            e.bind("<FocusOut>", lambda ev: self._schedule_changed())
            e.bind("<Return>", lambda ev: self._schedule_changed())
        self.sched_preview = ctk.CTkLabel(sch, text="", font=self.font(12), text_color=MUTED, anchor="w",
                                          justify="left", wraplength=390)
        self.sched_preview.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(8, 4))
        self.timeline = ctk.CTkCanvas(sch, height=64, bg=PANEL, highlightthickness=0, bd=0)
        self.timeline.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(6, 14))
        self.timeline.bind("<Configure>", lambda e: self._draw_timeline())

        # run
        run = self._panel(right, "Run", 0)
        run.grid_columnconfigure((0, 1), weight=1)
        run.grid_columnconfigure(2, weight=0)
        self.runs_label = self._stat(run, "Runs today", "0", 0)
        self.screens_label = self._stat(run, "Screens ready", "–", 1)
        btns = ctk.CTkFrame(run, fg_color="transparent")
        btns.grid(row=1, column=2, sticky="e", padx=16, pady=(0, 6))
        self.start_btn = self._button(btns, "▶  Start", self._start, primary=True, width=130, height=46)
        self.start_btn.grid(row=0, column=0, padx=(0, 8))
        self.stop_btn = self._button(btns, "■  Stop", self._stop, width=96, height=46, state="disabled")
        self.stop_btn.grid(row=0, column=1)
        opts = ctk.CTkFrame(run, fg_color="transparent")
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(6, 14))
        opts.grid_columnconfigure(0, weight=1)
        self.minimise = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts, text="Minimise UmaBot while it plays", variable=self.minimise, font=self.font(12),
                        text_color=MUTED, fg_color=CORAL, hover_color=CORAL_HOVER, border_color=BORDER,
                        checkbox_width=18, checkbox_height=18).grid(row=0, column=0, sticky="w")
        self._button(opts, "Check screen", self._test, width=112, height=28).grid(row=0, column=1, padx=(8, 0))
        self._button(opts, "Logs", self._open_logs, width=60, height=28).grid(row=0, column=2, padx=(8, 0))

        # activity
        logp = self._panel(right, "Activity", 1, expand=True)
        logp.grid_columnconfigure(0, weight=1)
        logp.grid_rowconfigure(1, weight=1)
        self.logbox = ctk.CTkTextbox(logp, fg_color=FIELD, text_color=BLUE_LIGHT, border_width=0, corner_radius=8,
                                     font=ctk.CTkFont(family="Consolas" if sys.platform == "win32" else "DejaVu Sans Mono",
                                                      size=12), wrap="word", height=120)
        self.logbox.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=12, pady=(0, 12))
        self.logbox.configure(state="disabled")

        # footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, columnspan=2, sticky="ew", padx=22, pady=(0, 12))
        foot.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(foot, text="Stop anytime: press Stop, or fling the mouse to the top-left corner of the screen.",
                     font=self.font(11), text_color=MUTED).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(foot, text=f"v{__version__}", font=self.font(11), text_color=MUTED).grid(
            row=0, column=1, sticky="e")

    def _stat(self, master, caption, value, col):
        box = ctk.CTkFrame(master, fg_color="transparent")
        box.grid(row=1, column=col, sticky="w", padx=16, pady=(0, 6))
        ctk.CTkLabel(box, text=caption, font=self.font(12), text_color=MUTED).grid(row=0, column=0, sticky="w")
        label = ctk.CTkLabel(box, text=value, font=self.font(26, "bold"), text_color=TEXT)
        label.grid(row=1, column=0, sticky="w")
        return label

    # ------------------------------------------------------------ helpers
    def _in_background(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _ui(self, fn):
        """Run fn on the UI thread (Tk isn't thread-safe)."""
        self.uiq.put(fn)

    def _set_pill(self, text, kind):
        self.pill.configure(text=f"●  {text}", text_color=KIND_COLORS.get(kind, MUTED))

    def _append_log(self, line):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n")
        if int(self.logbox.index("end-1c").split(".")[0]) > 600:
            self.logbox.delete("1.0", "100.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _fetch_fallback_buttons(self):
        try:
            tools.download(self.cfg, quiet=True, only_missing=True)
        except Exception as e:
            log.info(f"couldn't download the fallback buttons: {e}")
        self._ui(self._refresh_screens)

    # ------------------------------------------------------------ support card
    def _refresh_card(self):
        folder = os.path.join(self.base, "templates", "_card")
        path = next((os.path.join(folder, f) for f in ("card_full.png", "card.png")
                     if os.path.exists(os.path.join(folder, f))), None)
        if path:
            img = Image.open(path).convert("RGBA")
            k = min(66 / img.width, 88 / img.height)
            self._card_photo = ctk.CTkImage(light_image=img, dark_image=img,
                                            size=(max(1, round(img.width * k)), max(1, round(img.height * k))))
            self.card_img.configure(image=self._card_photo, text="")
            self.card_name.configure(text=self.cfg["card"].get("name") or "Custom card")
            self.card_msg.configure(text="Borrowed from the friend list at the start of every run.",
                                    text_color=MUTED)
        else:
            self.card_img.configure(image=None, text="?")
            self.card_name.configure(text="No card chosen yet")
            self.card_msg.configure(text="Find the card on GameTora, copy its link and paste it below.",
                                    text_color=MUTED)

    def _set_card(self):
        text = self.card_entry.get().strip()
        if not text:
            self.card_msg.configure(text="Paste a GameTora link or a card ID first.", text_color=AMBER)
            return
        self.card_btn.configure(state="disabled", text="…")
        self.card_msg.configure(text="Getting the card…", text_color=MUTED)

        def work():
            try:
                name = tools.set_card(self.cfg, text)
                log.info(f"support card set: {name}")
                self._ui(lambda: self._card_done(None))
            except Exception as e:
                self._ui(lambda err=str(e): self._card_done(err))
        self._in_background(work)

    def _card_done(self, error):
        self.card_btn.configure(state="normal", text="Set card")
        if error:
            self.card_msg.configure(text=error, text_color=RED)
        else:
            self.card_entry.delete(0, "end")
            self.focus()  # gives the entry its placeholder back
            self._refresh_card()
            self._refresh_screens()

    # ------------------------------------------------------------ schedule
    def _schedule_changed(self):
        start, end = self.sched_start.get().strip(), self.sched_end.get().strip()
        if not (TIME_RE.fullmatch(start) and TIME_RE.fullmatch(end)):
            self.sched_preview.configure(text="Use HH:MM, for example 10:30 and 01:00.", text_color=AMBER)
            return
        start, end = (f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in (start, end))
        self.sched_start.set(start)
        self.sched_end.set(end)
        config.save_settings(self.base, {"schedule": {"enabled": bool(self.sched_on.get()),
                                                      "start": start, "end": end}})
        self.cfg = config.load(self.cfg_path)
        self._refresh_schedule_preview()
        if self.thread:
            log.info("schedule saved: it applies the next time you press Start")

    def _draw_timeline(self):
        """Today at a glance: when it plays (coral), breaks and off hours (dark) and the time now (white)."""
        c = self.timeline
        k = ctk.ScalingTracker.get_widget_scaling(self)  # Tk canvases don't follow the display scaling
        if int(c.cget("height")) != round(64 * k):
            c.configure(height=round(64 * k))
        c.delete("all")
        w = c.winfo_width()
        if w < 60:
            return
        top, bottom = round(4 * k), round(16 * k)
        hx = lambda hours: k + (w - 2 * k) * max(0.0, min(24.0, hours)) / 24
        c.create_rectangle(hx(0), top, hx(24), bottom, fill=FIELD, outline="")
        s = self.cfg["schedule"]
        today = date.today()
        midnight = datetime.combine(today, dtime())
        hours = lambda t: (t - midnight).total_seconds() / 3600
        if s["enabled"]:
            sched = Schedule(s)
            for day in (today - timedelta(days=1), today):
                p = sched.plan(day)
                c.create_rectangle(hx(hours(p["start"])), top, hx(hours(p["end"])), bottom, fill=CORAL, outline="")
                for a, b in p["breaks"]:
                    c.create_rectangle(hx(hours(a)), top, hx(hours(b)), bottom, fill=FIELD, outline="")
        else:
            c.create_rectangle(hx(0), top, hx(24), bottom, fill=CORAL, outline="")
        now = hx(hours(datetime.now()))
        c.create_rectangle(now - k, top - 4 * k, now + k, bottom + 4 * k, fill=TEXT, outline="")
        font = (self.family or "TkDefaultFont", -round(11 * k))
        for h in (0, 6, 12, 18, 24):
            anchor = "nw" if h == 0 else "ne" if h == 24 else "n"
            c.create_text(hx(h), bottom + 6 * k, text=f"{h:02d}:00", fill=MUTED, font=font, anchor=anchor)
        y = bottom + 30 * k  # legend
        x = hx(0)
        for color, label in ((CORAL, "playing"), (FIELD, "break / off"), (TEXT, "now")):
            if label == "now":
                c.create_rectangle(x + 4 * k, y - 6 * k, x + 6 * k, y + 6 * k, fill=color, outline="")
            else:
                c.create_rectangle(x, y - 5 * k, x + 10 * k, y + 5 * k, fill=color, outline=BORDER)
            item = c.create_text(x + 16 * k, y, text=label, fill=MUTED, font=font, anchor="w")
            x = c.bbox(item)[2] + 18 * k

    def _refresh_schedule_preview(self):
        s = self.cfg["schedule"]
        self._draw_timeline()
        if not s["enabled"]:
            self.sched_preview.configure(text="Plays at any time of day.", text_color=MUTED)
            return
        p = Schedule(s).plan(date.today())
        breaks = ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in p["breaks"]) or "none"
        self.sched_preview.configure(
            text=f"Today: {p['start']:%H:%M} → {p['end']:%H:%M}  ·  breaks {breaks}  ·  shifts a bit every day",
            text_color=MUTED)

    # ------------------------------------------------------------ run
    def _refresh_screens(self):
        tpls = load_templates(self.base)
        rules = prepare_rules(self.cfg["rule"], lambda n: n in tpls, self.cfg["card"]["templates"])
        ready = sum(r.enabled for r in rules)
        self.screens_label.configure(text=f"{ready}/{len(rules)}", text_color=TEXT if ready == len(rules) else AMBER)

    def _set_running(self, running):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _start(self):
        if self.thread:
            return
        self.cfg = config.load(self.cfg_path)
        if not tools.has_card(self.cfg):
            self.card_msg.configure(text="Choose the support card first.", text_color=RED)
            self.card_entry.focus()
            return
        self.stop_event.clear()
        self.bot = None
        self._set_running(True)
        self._set_pill("Starting", "ok")
        cfg = self.cfg

        def work():
            bot = None
            try:
                bot = self.bot = Bot(cfg, sleep=self.stop_event.wait, should_stop=self.stop_event.is_set)
                bot.run()
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    log.info("stopped: mouse in the corner of the screen")
                    if bot:
                        bot.set_status("Stopped (mouse in the corner)", "stop")
                else:
                    log.info("the bot stopped because of an error:\n" + traceback.format_exc())
                    if bot:
                        bot.set_status("Stopped: error, see Activity", "error")
                    else:
                        self._ui(lambda: self._set_pill("Couldn't start, see Activity", "error"))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        if self.minimise.get():
            self.after(1500, self.iconify)

    def _stop(self):
        if self.thread:
            self.stop_event.set()
            self._set_pill("Stopping", "wait")

    def _test(self):
        def work():
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    tools.test(config.load(self.cfg_path))
            except SystemExit as e:
                out.write(f"{e.code}\n")
            except Exception as e:
                out.write(f"error: {e}\n")
            for line in out.getvalue().splitlines():
                if line.strip():
                    self.logq.put(line)
        log.info("checking the screen…")
        self._in_background(work)

    def _open_logs(self):
        folder = os.path.join(self.base, "logs")
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            webbrowser.open(f"file://{folder}")

    def _poll(self):
        while not self.uiq.empty():
            self.uiq.get_nowait()()
        for _ in range(200):
            try:
                self._append_log(self.logq.get_nowait())
            except queue.Empty:
                break
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 150 == 0:  # ~ once a minute: move the "now" tick
            self._draw_timeline()
        if self.bot:
            self._set_pill(self.bot.status, self.bot.status_kind)
            self.runs_label.configure(text=str(self.bot.runs_today))
        if self.thread and not self.thread.is_alive():
            self.thread = None
            self._set_running(False)
            if self.bot and self.bot.status_kind in ("ok", "wait"):
                self.bot.set_status("Stopped", "stop")
            if self.state() == "iconic":
                self.deiconify()  # show the app again when the bot stops
        self.after(400, self._poll)

    def _on_close(self):
        self.stop_event.set()
        log.removeHandler(self._handler)
        self.destroy()


def run(cfg_path=None):
    App(cfg_path).mainloop()


def selftest(cfg_path=None):
    """Builds the whole window without showing it (used by CI to check the .exe)."""
    app = App(cfg_path)
    app.withdraw()
    app.update()
    app._on_close()
    return 0
