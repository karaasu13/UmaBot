"""What UmaBot did, per day (kept in stats.json, so reopening UmaBot doesn't lose the day): runs, TP and
Toughness spent, fans earned, alerts. Plus the last TP it read on screen, to estimate it at any time."""
import json
import os
import re
from datetime import datetime, timedelta

DAY = {"runs": 0, "tp_spent": 0, "toughness": 0, "fans": 0, "fan_runs": 0, "last_fans": 0, "alerts": 0}


def fans_gained(words):
    """'Total Fans' / '97,871,814(+1,028,385)' on the rewards screen -> 1028385."""
    total = next((w for w in words if w.key == "totalfans"), None)
    candidates = [w for w in words if re.search(r"\(\+[\d,.]+\)", w.text)]
    if total is not None:
        below = [w for w in candidates if w.box[1] >= total.box[1] - 5]
        candidates = sorted(below, key=lambda w: (w.box[1] - total.box[1]) ** 2 + (w.box[0] - total.box[0]) ** 2)
    elif candidates:
        return None  # not the rewards screen
    for w in candidates:
        m = re.search(r"\(\+([\d,.]+)\)", w.text)
        n = int(re.sub(r"[,.]", "", m.group(1)))
        if n >= 1000:  # the bond level also shows (+15)
            return n
    return None


class Stats:
    def __init__(self, base, now=datetime.now):
        self.path = os.path.join(base, "stats.json")
        self.now = now
        self.started = now()
        self.session_runs = 0
        self.tp = None          # (value, when read, max)
        self.toughness = None   # (held, when read)
        self.days = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        keep = dict(sorted(self.days.items())[-60:])  # two months is plenty
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(keep, f, indent=1)
        except OSError:
            pass

    def today(self):
        key = f"{self.now():%Y-%m-%d}"
        day = self.days.setdefault(key, dict(DAY))
        for k, v in DAY.items():
            day.setdefault(k, v)
        return day

    # ---------------------------------------------------------------- recording
    def run(self, cost):
        d = self.today()
        d["runs"] += 1
        d["tp_spent"] += cost or 0
        self.session_runs += 1
        if self.tp is not None and cost:
            value, _, top = self.tp_estimate()
            self.tp = (max(0, value - cost), self.now(), top)
        self._save()

    def fans(self, n):
        d = self.today()
        d["fans"] += n
        d["fan_runs"] += 1
        d["last_fans"] = n
        self._save()

    def used_toughness(self, recovery=30):
        d = self.today()
        d["toughness"] += 1
        if self.toughness:
            self.toughness = (max(0, self.toughness[0] - 1), self.now())
        if self.tp is not None:
            value, _, top = self.tp_estimate()
            self.tp = (value + recovery, self.now(), top)
        self._save()

    def alert(self):
        self.today()["alerts"] += 1
        self._save()

    def saw_tp(self, value, top=100):
        if value is not None:
            self.tp = (value, self.now(), top or 100)

    def saw_toughness(self, held):
        if held is not None:
            self.toughness = (held, self.now())

    # ---------------------------------------------------------------- reading
    def tp_estimate(self, regen_min=10):
        """(TP now, when it was last read, the maximum): +1 every regen_min minutes since, up to the maximum."""
        if self.tp is None:
            return None
        value, when, top = self.tp
        if value >= top:
            return value, when, top
        grown = int((self.now() - when).total_seconds() // (regen_min * 60))
        return min(top, value + grown), when, top

    def tp_text(self, cost, regen_min=10):
        est = self.tp_estimate(regen_min)
        if est is None:
            return "I haven't seen the TP yet (it shows on the home screen and on Final Confirmation)."
        value, when, top = est
        read = f"read at {when:%H:%M}" + (", +1 every 10 min since" if self.now() - when > timedelta(minutes=10) else "")
        runs = value // cost if cost else 0
        text = f"TP: about {value}/{top} ({read}). Enough for {runs} run{'s' if runs != 1 else ''} at {cost} TP."
        if value < cost:
            at = self.now() + timedelta(minutes=(cost - value) * regen_min)
            text += f" Next run possible around {at:%H:%M}."
        if self.toughness:
            text += f" Toughness 30: {self.toughness[0]}."
        return text

    def summary(self, status=None, cost=None):
        d = self.today()
        hours = max((self.now() - self.started).total_seconds() / 3600, 1 / 60)
        fans = f"{d['fans']:,}".replace(",", " ")
        lines = [f"**Today** ({self.now():%d/%m}): {d['runs']} run{'s' if d['runs'] != 1 else ''}, {fans} fans.",
                 f"Spent: {d['tp_spent']} TP, {d['toughness']} Toughness 30, 0 carats.",
                 f"Since UmaBot started ({self.started:%H:%M}): {self.session_runs} runs, "
                 f"{self.session_runs / hours:.1f} an hour."]
        if d["alerts"]:
            lines.append(f"Times it needed help: {d['alerts']}.")
        if cost:
            lines.append(self.tp_text(cost))
        if status:
            lines.append(f"Now: {status}.")
        return "\n".join(lines)
