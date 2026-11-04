"""UmaBot desktop app.

Design: a race-day board. Light chalk background, navy ink from the icon, turf for when it plays and
coral (the icon's arrow) for the one thing you press. Today is drawn as a race track: the start gate is
when it begins, the finish post is when it stops, breaks are hatched, and the coral dot is "now".
"""
import contextlib
import io
import logging
import os
import queue
import re
import sys
import threading
import traceback
import tkinter
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

# ---------------------------------------------------------------- design tokens (light, dark)
INK = ("#16213B", "#E6ECF5")        # text: navy from the icon / its light counterpart
INK_SOFT = ("#5B6880", "#93A3BD")   # secondary text
CHALK = ("#F1F4F2", "#0C1526")      # window: chalk / the icon's navy
PAPER = ("#FFFFFF", "#131E35")      # surfaces
LINE = ("#DCE3DF", "#24324F")       # hairlines and field borders
TURF = ("#3B9A5E", "#3FA566")       # the track while it plays
TURF_PALE = ("#D3E6D8", "#1A2F31")  # the track while it's closed
HATCH = ("#B9D5C1", "#284640")      # breaks
RAIL = ("#FFFFFF", "#CFDAEA")       # the track's rails
CORAL = ("#DB6470", "#E1707B")      # the runner and the main button (the icon's arrow)
CORAL_HOVER = ("#C9545F", "#CD5D68")
SOLID = ("#16213B", "#E6ECF5")      # the dark button (Stop, Use card)
SOLID_HOVER = ("#24315A", "#FFFFFF")
ON_SOLID = ("#FFFFFF", "#0C1526")
BOW = ("#3E6EC2", "#86A6E8")        # links and focus (the icon's bow)
PAPER_TEXT = ("#FFFFFF", "#FFFFFF")  # text on coral
GOLD = ("#B57A12", "#E3AE49")       # waiting
RED = ("#C2413B", "#EF6B63")        # errors
KIND = {"ok": TURF, "wait": GOLD, "stop": INK_SOFT, "error": RED}

GAMETORA_SUPPORTS = "https://gametora.com/umamusume/supports"
TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")


def resource(rel):
    """Files bundled with the app (inside the .exe, or in the repo when running from source)."""
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, rel)


def animations_enabled():
    """Respect Windows' "Show animations" setting."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        on = ctypes.c_bool()
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(on.value)
    except Exception:
        return True


def mix(a, b, t):
    """Colour between a and b (0 <= t <= 1)."""
    pa, pb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


class _ToQueue(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put((datetime.fromtimestamp(record.created), record.getMessage()))


class App(ctk.CTk):
    def __init__(self, cfg_path=None):
        self.cfg_path = cfg_path
        self.cfg = config.load(cfg_path)
        ctk.set_appearance_mode(self.cfg.get("appearance", "dark"))
        super().__init__(fg_color=CHALK)
        self.base = self.cfg["_base"]
        self.bot, self.thread = None, None
        self.stop_event = threading.Event()
        self.logq, self.uiq = queue.Queue(), queue.Queue()
        self.editing_card = False
        self.pulse = 0.0
        self.animate = animations_enabled()

        setup_logging(self.base)
        self._handler = _ToQueue(self.logq)
        log.addHandler(self._handler)
        self._load_fonts()

        self.title("UmaBot")
        self.geometry("980x640")
        self.minsize(900, 600)
        self._set_icon()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F8>", lambda e: self._toggle())

        self._apply_mode()
        self._refresh_card()
        self._refresh_schedule()
        self._refresh_status()
        if tools.missing_fallback(self.cfg):
            self._in_background(self._fetch_fallback_buttons)
        if tools.has_card(self.cfg):
            log.info("Ready. Open Umamusume on the home screen and press Start.")
        else:
            log.info("Pick the support card to borrow, then press Start.")
        self.after(300, self._poll)
        self.after(80, self._tick)

    # ------------------------------------------------------------ type
    def _load_fonts(self):
        folder = resource(os.path.join("assets", "fonts"))
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.lower().endswith(".ttf"):
                    ctk.FontManager.load_font(os.path.join(folder, f))
        families = set(self.tk.call("font", "families"))
        if "Barlow Condensed SemiBold" in families or "Barlow Condensed" in families:
            self.f_body, self.f_medium, self.f_strong = "Barlow", "Barlow Medium", "Barlow SemiBold"
            self.f_display, self.f_display_medium = "Barlow Condensed SemiBold", "Barlow Condensed Medium"
        else:  # fonts couldn't be loaded: system fallback
            fb = "Segoe UI" if sys.platform == "win32" else None
            self.f_body = self.f_medium = self.f_strong = self.f_display = self.f_display_medium = fb

    def font(self, family, size):
        return ctk.CTkFont(family=family, size=size)

    @staticmethod
    def col(token):
        """The colour of a (light, dark) token in the current mode, for plain Tk widgets."""
        return token[1] if ctk.get_appearance_mode() == "Dark" else token[0]

    def _switch_mode(self):
        mode = "light" if ctk.get_appearance_mode() == "Dark" else "dark"
        ctk.set_appearance_mode(mode)
        config.save_settings(self.base, {"appearance": mode})
        self._apply_mode()

    def _apply_mode(self):
        dark = ctk.get_appearance_mode() == "Dark"
        self.mode_link.configure(text="Light mode" if dark else "Dark mode")
        self.track.configure(bg=self.col(CHALK))
        for hairline in (self.rules_h, self.rules_v):
            hairline.configure(bg=self.col(LINE))
        self.logbox.tag_config("time", foreground=self.col(INK_SOFT))
        self.logbox.tag_config("strong", foreground=self.col(INK))
        self._draw_track()

    def _set_icon(self):
        try:
            if sys.platform == "win32":
                ico = resource(os.path.join("assets", "icon.ico"))
                self.iconbitmap(ico)
                self.after(250, lambda: self.iconbitmap(ico))  # customtkinter resets it on start
            else:
                self._icon_photo = tkinter.PhotoImage(file=resource(os.path.join("assets", "icon.png")))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    # ------------------------------------------------------------ layout
    def _label(self, master, text="", family=None, size=14, color=INK, **kw):
        return ctk.CTkLabel(master, text=text, font=self.font(family or self.f_body, size), text_color=color, **kw)

    def _link(self, master, text, command, size=13):
        lab = ctk.CTkLabel(master, text=text, font=self.font(self.f_medium, size), text_color=BOW, cursor="hand2")
        lab.bind("<Button-1>", lambda e: command())
        lab.bind("<Enter>", lambda e: lab.configure(text_color=INK))
        lab.bind("<Leave>", lambda e: lab.configure(text_color=BOW))
        return lab

    def _entry(self, master, **kw):
        e = ctk.CTkEntry(master, fg_color=PAPER, border_color=LINE, border_width=1, corner_radius=8,
                         text_color=INK, placeholder_text_color=INK_SOFT, font=self.font(self.f_medium, 14), **kw)
        e.bind("<FocusIn>", lambda ev: e.configure(border_color=BOW, border_width=2), add="+")
        e.bind("<FocusOut>", lambda ev: e.configure(border_color=LINE, border_width=1), add="+")
        return e

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)
        pad = 28

        # header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=pad, pady=(18, 0))
        head.grid_columnconfigure(1, weight=1)
        try:
            logo = Image.open(resource(os.path.join("assets", "icon.png")))
            self._logo = ctk.CTkImage(light_image=logo, dark_image=logo, size=(34, 34))
            ctk.CTkLabel(head, image=self._logo, text="").grid(row=0, column=0, padx=(0, 10))
        except Exception:
            pass
        self._label(head, "UmaBot", self.f_display, 24).grid(row=0, column=1, sticky="w")
        self.mode_link = self._link(head, "", self._switch_mode, size=12)
        self.mode_link.grid(row=0, column=2, sticky="e", padx=(0, 16))
        self._label(head, f"v{__version__}", self.f_body, 12, INK_SOFT).grid(row=0, column=3, sticky="e")

        # status + the one button
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", padx=pad, pady=(22, 10))
        top.grid_columnconfigure(1, weight=1)
        self.status_bar = ctk.CTkFrame(top, width=5, height=44, corner_radius=3, fg_color=INK_SOFT)
        self.status_bar.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14), pady=(4, 4))
        self.headline = self._label(top, "", self.f_display, 36, anchor="w")
        self.headline.grid(row=0, column=1, sticky="ew")
        self.subline = self._label(top, "", self.f_body, 14, INK_SOFT, anchor="w", justify="left")
        self.subline.grid(row=1, column=1, sticky="ew")
        self.setup_line = self._label(top, "", self.f_medium, 13, GOLD, anchor="w", justify="left")
        self.setup_line.grid(row=2, column=1, sticky="ew", pady=(2, 0))

        act = ctk.CTkFrame(top, fg_color="transparent")
        act.grid(row=0, column=2, rowspan=3, sticky="e")
        self.toggle_btn = ctk.CTkButton(act, text="Start", command=self._toggle, width=200, height=56,
                                        corner_radius=12, fg_color=CORAL, hover_color=CORAL_HOVER,
                                        text_color=PAPER_TEXT, text_color_disabled=("#F4D5D8", "#5C6A85"),
                                        font=self.font(self.f_display, 22))
        self.toggle_btn.grid(row=0, column=0, sticky="e")
        self.minimise = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(act, text="Hide UmaBot while it plays", variable=self.minimise,
                        font=self.font(self.f_body, 13), text_color=INK_SOFT, fg_color=SOLID, hover_color=SOLID,
                        checkmark_color=ON_SOLID,
                        border_color=LINE, border_width=2, checkbox_width=18, checkbox_height=18,
                        corner_radius=4).grid(row=1, column=0, sticky="e", pady=(10, 0))

        # today's track
        self.track = ctk.CTkCanvas(self, height=96, bg=self.col(CHALK), highlightthickness=0, bd=0)
        self.track.grid(row=2, column=0, sticky="ew", padx=pad, pady=(14, 0))
        self.track.bind("<Configure>", lambda e: self._draw_track())

        # schedule, written as a sentence
        sch = ctk.CTkFrame(self, fg_color="transparent")
        sch.grid(row=3, column=0, sticky="ew", padx=pad, pady=(6, 0))
        sch.grid_columnconfigure(5, weight=1)
        s = self.cfg["schedule"]
        self.sched_on = ctk.BooleanVar(value=bool(s["enabled"]))
        self.sched_start = ctk.StringVar(value=s["start"])
        self.sched_end = ctk.StringVar(value=s["end"])
        self.sched_lead = self._label(sch, "Plays from", self.f_body, 14)
        self.sched_lead.grid(row=0, column=0, padx=(0, 8))
        self.start_entry = self._entry(sch, textvariable=self.sched_start, width=66, height=32, justify="center")
        self.start_entry.grid(row=0, column=1)
        self.sched_to = self._label(sch, "to", self.f_body, 14)
        self.sched_to.grid(row=0, column=2, padx=8)
        self.end_entry = self._entry(sch, textvariable=self.sched_end, width=66, height=32, justify="center")
        self.end_entry.grid(row=0, column=3)
        self.sched_tail = self._label(sch, "", self.f_body, 14, INK_SOFT, anchor="w")
        self.sched_tail.grid(row=0, column=4, padx=(10, 0), sticky="w")
        for e in (self.start_entry, self.end_entry):
            e.bind("<FocusOut>", lambda ev: self._schedule_changed(), add="+")
            e.bind("<Return>", lambda ev: self._schedule_changed())
        ctk.CTkSwitch(sch, text="Only at these hours", variable=self.sched_on, command=self._schedule_changed,
                      font=self.font(self.f_body, 13), text_color=INK_SOFT, progress_color=TURF,
                      button_color=RAIL, button_hover_color=RAIL, fg_color=LINE).grid(row=0, column=6, sticky="e")

        self.rules_h = tkinter.Frame(self, height=1, bd=0, highlightthickness=0)  # plain hairline
        self.rules_h.grid(row=4, column=0, sticky="ew", padx=pad, pady=(22, 0))

        # bottom: the card on the left, what happened on the right
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="nsew", padx=pad, pady=(16, 22))
        bottom.grid_columnconfigure(2, weight=1)
        bottom.grid_rowconfigure(0, weight=1)
        self._build_card(bottom)
        self.rules_v = tkinter.Frame(bottom, width=1, bd=0, highlightthickness=0)
        self.rules_v.grid(row=0, column=1, sticky="ns", padx=24)
        self._build_activity(bottom)

    def _build_card(self, master):
        box = ctk.CTkFrame(master, fg_color="transparent", width=360)
        box.grid(row=0, column=0, sticky="nsw")
        box.grid_columnconfigure(1, weight=1)
        self._label(box, "Support card", self.f_display, 18).grid(row=0, column=0, columnspan=2, sticky="w")
        self.card_img = ctk.CTkLabel(box, text="", width=72, height=96, fg_color=PAPER, corner_radius=8)
        self.card_img.grid(row=1, column=0, rowspan=3, sticky="nw", pady=(10, 0), padx=(0, 16))
        self.card_name = self._label(box, "", self.f_strong, 16, anchor="w", justify="left", wraplength=250)
        self.card_name.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        self.card_note = self._label(box, "", self.f_body, 13, INK_SOFT, anchor="w", justify="left",
                                     wraplength=250)
        self.card_note.grid(row=2, column=1, sticky="ew", pady=(2, 0))
        self.card_change = self._link(box, "Change card", self._edit_card)
        self.card_change.grid(row=3, column=1, sticky="nw", pady=(6, 0))

        self.card_form = ctk.CTkFrame(box, fg_color="transparent")
        self.card_form.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.card_form.grid_columnconfigure(0, weight=1)
        self.card_entry = self._entry(self.card_form, placeholder_text="GameTora link or card ID", height=36)
        self.card_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.card_entry.bind("<Return>", lambda e: self._set_card())
        self.card_btn = ctk.CTkButton(self.card_form, text="Use card", command=self._set_card, width=92, height=36,
                                      corner_radius=8, fg_color=SOLID, hover_color=SOLID_HOVER, text_color=ON_SOLID,
                                      font=self.font(self.f_strong, 14))
        self.card_btn.grid(row=0, column=1)
        hint = ctk.CTkFrame(self.card_form, fg_color="transparent")
        hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.card_msg = self._label(hint, "Copy the card's address on", self.f_body, 13, INK_SOFT)
        self.card_msg.grid(row=0, column=0, sticky="w")
        self.card_site = self._link(hint, "GameTora", lambda: webbrowser.open(GAMETORA_SUPPORTS))
        self.card_site.grid(row=0, column=1, sticky="w", padx=(4, 0))

    def _build_activity(self, master):
        box = ctk.CTkFrame(master, fg_color="transparent")
        box.grid(row=0, column=2, sticky="nsew")
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(1, weight=1)
        self._label(box, "Activity", self.f_display, 18).grid(row=0, column=0, sticky="w")
        links = ctk.CTkFrame(box, fg_color="transparent")
        links.grid(row=0, column=1, sticky="e")
        self._link(links, "Check the game screen", self._test).grid(row=0, column=0, padx=(0, 16))
        self._link(links, "Open logs", self._open_logs).grid(row=0, column=1)
        self.logbox = ctk.CTkTextbox(box, fg_color=PAPER, text_color=INK, border_width=0, corner_radius=10,
                                     font=self.font(self.f_body, 13), wrap="word", height=120,
                                     scrollbar_button_color=LINE, scrollbar_button_hover_color=INK_SOFT)
        self.logbox.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        self.logbox.configure(state="disabled")

    # ------------------------------------------------------------ the track
    def _draw_track(self):
        c = self.track
        C = self.col
        k = ctk.ScalingTracker.get_widget_scaling(self)  # Tk canvases don't follow the display scaling
        if int(c.cget("height")) != round(96 * k):
            c.configure(height=round(96 * k))
        c.delete("all")
        w = c.winfo_width()
        if w < 100:
            return
        top, bottom = 26 * k, 62 * k
        mid, radius = (top + bottom) / 2, (bottom - top) / 2
        left, right = radius, w - radius
        hx = lambda h: left + (right - left) * max(0.0, min(24.0, h)) / 24
        small = (self.f_display_medium or "TkDefaultFont", -round(12 * k))
        strong = (self.f_display or "TkDefaultFont", -round(13 * k))

        # the track itself: a closed straight with rounded ends
        c.create_rectangle(left, top, right, bottom, fill=C(TURF_PALE), outline="")
        for x in (left, right):
            c.create_oval(x - radius, top, x + radius, bottom, fill=C(TURF_PALE), outline="")

        s = self.cfg["schedule"]
        today = date.today()
        midnight = datetime.combine(today, dtime())
        hours = lambda t: (t - midnight).total_seconds() / 3600
        plan = None
        if s["enabled"]:
            sched = Schedule(s)
            for day in (today - timedelta(days=1), today):
                p = sched.plan(day)
                a, b = hours(p["start"]), hours(p["end"])
                if b <= 0 or a >= 24:
                    continue
                c.create_rectangle(hx(a), top, hx(b), bottom, fill=C(TURF), outline="")
                for ba, bb in p["breaks"]:
                    self._hatch(hx(hours(ba)), hx(hours(bb)), top, bottom)
                if day == today:
                    plan = (a, min(b, 24.0), p["start"], p["end"])
        else:
            c.create_rectangle(left, top, right, bottom, fill=C(TURF), outline="")

        # rails and furlong posts
        for y in (top + 5 * k, bottom - 5 * k):
            c.create_line(left, y, right, y, fill=C(PAPER), width=max(1, round(1.5 * k)))
        for h in range(0, 25, 3):
            x = hx(h)
            c.create_line(x, bottom + 3 * k, x, bottom + 9 * k, fill=C(INK_SOFT), width=max(1, round(k)))
            c.create_text(x, bottom + 12 * k, text=f"{h:02d}", fill=C(INK_SOFT), font=small, anchor="n")

        # runs done today: small white marks on the lower rail
        for t in (self.bot.run_times if self.bot else []):
            x = hx(hours(t))
            c.create_oval(x - 3 * k, bottom - 8 * k, x + 3 * k, bottom - 2 * k, fill=C(RAIL), outline="")

        # start gate and finish post, with their times above
        now_h = hours(datetime.now())
        labels = []
        if plan:
            a, b, t_start, t_end = plan
            if a > 0:
                c.create_rectangle(hx(a) - 2 * k, top - 4 * k, hx(a) + 2 * k, bottom + 4 * k, fill=C(INK), outline="")
                labels.append((hx(a), f"{t_start:%H:%M}", C(INK)))
            sq = 4 * k
            rows = int((bottom - top + 8 * k) // sq)
            for row in range(rows):  # chequered finish post
                for col in range(2):
                    y = top - 4 * k + row * sq
                    c.create_rectangle(hx(b) - sq + col * sq, y, hx(b) + col * sq, y + sq,
                                       fill=C(INK) if (col + row) % 2 == 0 else C(CHALK), outline="")
            labels.append((hx(b), f"{t_end:%H:%M}", C(INK)))

        # the runner: now
        x = hx(now_h)
        if self.thread and self.animate:
            playing = not s["enabled"] or Schedule(s).state(datetime.now())[0] == "active"
            r = (9 + 9 * self.pulse) * k
            c.create_oval(x - r, mid - r, x + r, mid + r, width=max(1, round(2 * k)),
                          outline=mix(C(CORAL), C(TURF) if playing else C(TURF_PALE), self.pulse))
        c.create_oval(x - 9 * k, mid - 9 * k, x + 9 * k, mid + 9 * k, fill=C(CORAL), outline=C(CHALK),
                      width=max(1, round(2.5 * k)))
        c.create_text(x, top - 8 * k, text="now", fill=C(CORAL), font=strong, anchor="s")
        for lx, text, color in labels:
            if abs(lx - x) > 34 * k:  # don't let the gate/finish times collide with "now"
                c.create_text(lx, top - 8 * k, text=text, fill=color, font=small, anchor="s")

    def _hatch(self, x0, x1, top, bottom):
        c = self.track
        c.create_rectangle(x0, top, x1, bottom, fill=self.col(TURF_PALE), outline="")
        step, h = 7, bottom - top
        x = x0 - h
        while x < x1:
            ax, ay, bx, by = x, bottom, x + h, top
            if ax < x0:  # clip the diagonal to the break
                ay -= (x0 - ax)
                ax = x0
            if bx > x1:
                by += (bx - x1)
                bx = x1
            if bx > ax:
                c.create_line(ax, ay, bx, by, fill=self.col(HATCH), width=2)
            x += step

    def _tick(self):
        """The runner's pulse while the bot plays (the one animation in the app)."""
        if self.thread and self.animate:
            self.pulse = (self.pulse + 0.045) % 1.0
            self._draw_track()
        self.after(70 if self.thread else 1000, self._tick)

    # ------------------------------------------------------------ helpers
    def _in_background(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _ui(self, fn):
        """Run fn on the UI thread (Tk isn't thread-safe)."""
        self.uiq.put(fn)

    def _append_log(self, when, msg):
        strong = msg.startswith(("run #", "Stopped", "stopped", "Ready", "Pick", "UmaBot starting"))
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"{when:%H:%M}  ", "time")
        self.logbox.insert("end", msg[:1].upper() + msg[1:] + "\n", "strong" if strong else ())
        if int(self.logbox.index("end-1c").split(".")[0]) > 600:
            self.logbox.delete("1.0", "100.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _fetch_fallback_buttons(self):
        try:
            tools.download(self.cfg, quiet=True, only_missing=True)
        except Exception as e:
            log.info(f"couldn't download the fallback buttons: {e}")
        self._ui(self._refresh_status)

    # ------------------------------------------------------------ status
    def _refresh_status(self):
        tpls = load_templates(self.base)
        rules = prepare_rules(self.cfg["rule"], lambda n: n in tpls, self.cfg["card"]["templates"])
        ready, total = sum(r.enabled for r in rules), len(rules)
        self.setup_line.configure(
            text="" if ready == total else f"{ready} of {total} game screens are set up. The others are skipped for now.")
        running = self.thread is not None
        if running and self.bot:
            head, kind = self.bot.status, self.bot.status_kind
        elif self.bot and self.bot.status_kind == "error":
            head, kind = self.bot.status, "error"
        elif tools.has_card(self.cfg):
            head, kind = "Not running", "stop"
        else:
            head, kind = "Pick a support card", "wait"
        self.headline.configure(text=head)
        self.status_bar.configure(fg_color=KIND.get(kind, INK_SOFT))
        n = self.bot.runs_today if self.bot else 0
        if running:
            sub = "No runs yet today." if n == 0 else f"{n} run{'s' if n != 1 else ''} today."
        elif not tools.has_card(self.cfg):
            sub = "UmaBot borrows it from the friend list at the start of every run."
        else:
            sub = "Open Umamusume on the home screen, then press Start. F8 starts and stops."
        self.subline.configure(text=sub)

    def _toggle(self):
        if self.thread:
            self._stop()
        else:
            self._start()

    def _set_running(self, running, stopping=False):
        if stopping:
            self.toggle_btn.configure(text="Stopping…", state="disabled", fg_color=INK_SOFT)
        elif running:
            self.toggle_btn.configure(text="Stop", state="normal", fg_color=SOLID, hover_color=SOLID_HOVER,
                                      text_color=ON_SOLID)
        else:
            self.toggle_btn.configure(text="Start", state="normal", fg_color=CORAL, hover_color=CORAL_HOVER,
                                      text_color=PAPER_TEXT)

    # ------------------------------------------------------------ support card
    def _refresh_card(self):
        folder = os.path.join(self.base, "templates", "_card")
        path = next((os.path.join(folder, f) for f in ("card_full.png", "card.png")
                     if os.path.exists(os.path.join(folder, f))), None)
        if path:
            img = Image.open(path).convert("RGBA")
            k = min(72 / img.width, 96 / img.height)
            self._card_photo = ctk.CTkImage(light_image=img, dark_image=img,
                                            size=(max(1, round(img.width * k)), max(1, round(img.height * k))))
            self.card_img.configure(image=self._card_photo, text="", fg_color="transparent")
            self.card_name.configure(text=self.cfg["card"].get("name") or "Your card")
            self.card_note.configure(text="Borrowed from the friend list at the start of every run.")
            self.card_change.grid()
            show_form = self.editing_card
        else:
            self._card_photo = None
            self.card_img.configure(image=None, text="No card", fg_color=PAPER, text_color=INK_SOFT,
                                    font=self.font(self.f_medium, 12))
            self.card_name.configure(text="None yet")
            self.card_note.configure(text="Any card you can borrow from a friend works.")
            self.card_change.grid_remove()
            show_form = True
        if show_form:
            self.card_form.grid()
        else:
            self.card_form.grid_remove()

    def _edit_card(self):
        self.editing_card = not self.editing_card
        self._refresh_card()
        if self.editing_card:
            self.card_entry.focus()

    def _set_card(self):
        text = self.card_entry.get().strip()
        if not text:
            self._card_hint("Paste the card's GameTora address or its ID first.", RED)
            return
        self.card_btn.configure(state="disabled", text="Getting…")

        def work():
            try:
                name = tools.set_card(self.cfg, text)
                log.info(f"support card set: {name}")
                self._ui(lambda: self._card_done(None))
            except Exception as e:
                self._ui(lambda err=str(e): self._card_done(err))
        self._in_background(work)

    def _card_hint(self, text, color):
        self.card_msg.configure(text=text, text_color=color)
        if color == RED:
            self.card_site.grid_remove()
        else:
            self.card_site.grid()

    def _card_done(self, error):
        self.card_btn.configure(state="normal", text="Use card")
        if error:
            self._card_hint(error[:1].upper() + error[1:], RED)
            return
        self.card_entry.delete(0, "end")
        self.focus()  # gives the entry its placeholder back
        self.editing_card = False
        self._card_hint("Copy the card's address on", INK_SOFT)
        self._refresh_card()
        self._refresh_status()

    # ------------------------------------------------------------ schedule
    def _schedule_changed(self):
        start, end = self.sched_start.get().strip(), self.sched_end.get().strip()
        on = bool(self.sched_on.get())
        if on and not (TIME_RE.fullmatch(start) and TIME_RE.fullmatch(end)):
            self.sched_tail.configure(text="Use hours like 10:30 and 01:00.", text_color=RED)
            return
        if TIME_RE.fullmatch(start) and TIME_RE.fullmatch(end):
            start, end = (f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in (start, end))
            self.sched_start.set(start)
            self.sched_end.set(end)
        config.save_settings(self.base, {"schedule": {"enabled": on, "start": start, "end": end}})
        self.cfg = config.load(self.cfg_path)
        self._refresh_schedule()
        if self.thread:
            log.info("schedule saved, it applies the next time you press Start")

    def _refresh_schedule(self):
        on = self.cfg["schedule"]["enabled"]
        for w in (self.sched_lead, self.sched_to, self.start_entry, self.end_entry):
            w.grid() if on else w.grid_remove()
        if on:
            self.sched_tail.configure(text="with a few breaks. The times shift a little every day.",
                                      text_color=INK_SOFT)
        else:
            self.sched_tail.configure(text="Plays at any time of day.", text_color=INK_SOFT)
        self._draw_track()

    # ------------------------------------------------------------ run
    def _start(self):
        if self.thread:
            return
        self.cfg = config.load(self.cfg_path)
        if not tools.has_card(self.cfg):
            self.editing_card = True
            self._refresh_card()
            self._card_hint("Pick the support card first.", RED)
            self.card_entry.focus()
            return
        self.stop_event.clear()
        self.bot = None
        self._set_running(True)
        cfg = self.cfg

        def work():
            bot = None
            try:
                bot = self.bot = Bot(cfg, sleep=self.stop_event.wait, should_stop=self.stop_event.is_set)
                bot.run()
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    log.info("Stopped: the mouse went into the corner of the screen")
                    if bot:
                        bot.set_status("Stopped", "stop")
                else:
                    log.info("Stopped because of an error:\n" + traceback.format_exc())
                    if bot:
                        bot.set_status("Stopped by an error", "error")
                    else:
                        self._ui(lambda: self.headline.configure(text="Couldn't start"))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self._refresh_status()
        if self.minimise.get():
            self.after(1500, self.iconify)

    def _stop(self):
        if self.thread:
            self.stop_event.set()
            self._set_running(True, stopping=True)

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
                    self.logq.put((datetime.now(), line.strip()))
        log.info("checking the game screen…")
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
                self._append_log(*self.logq.get_nowait())
            except queue.Empty:
                break
        if self.thread and not self.thread.is_alive():
            self.thread = None
            self._set_running(False)
            if self.bot and self.bot.status_kind in ("ok", "wait"):
                self.bot.set_status("Stopped", "stop")
            if self.state() == "iconic":
                self.deiconify()  # show the app again when the bot stops
            self._draw_track()
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self.thread or self._ticks % 5 == 0:
            self._refresh_status()
        if not self.thread and self._ticks % 150 == 0:  # ~ once a minute: move "now"
            self._draw_track()
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
    app._draw_track()
    app._on_close()
    return 0
