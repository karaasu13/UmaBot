"""Human-looking schedule: a different active window every day, plus random breaks.

Each day's plan is deterministic (same seed + same date = same plan),
so restarting the bot doesn't reshuffle the day.
"""
import hashlib
import platform
import random
from datetime import datetime, timedelta


def _hm(text):
    h, m = text.split(":")
    return int(h), int(m)


def machine_seed():
    return int(hashlib.sha256(platform.node().encode()).hexdigest()[:8], 16)


class Schedule:
    def __init__(self, cfg_schedule, seed=None):
        self.cfg = cfg_schedule
        self.seed = machine_seed() if seed is None else seed

    def plan(self, day):
        """Dict with start, end (datetime) and breaks [(start, end)] for `day` (a date)."""
        c = self.cfg
        rng = random.Random(day.toordinal() * 7919 + self.seed)
        j = c["jitter_min"]
        h, m = _hm(c["start"])
        base_start = datetime(day.year, day.month, day.day, h, m)
        h, m = _hm(c["end"])
        base_end = datetime(day.year, day.month, day.day, h, m)
        if base_end <= base_start:  # goes past midnight
            base_end += timedelta(days=1)
        start = base_start + timedelta(minutes=rng.uniform(-j, j))
        end = base_end + timedelta(minutes=rng.uniform(-j, j))

        breaks = []
        n = rng.randint(*c["breaks_per_day"])
        lo, hi = start + timedelta(minutes=60), end - timedelta(minutes=90)
        if hi > lo:
            span = (hi - lo).total_seconds()
            for _ in range(n):
                b_start = lo + timedelta(seconds=rng.uniform(0, span))
                b_end = b_start + timedelta(minutes=rng.uniform(*c["break_length_min"]))
                breaks.append((b_start, min(b_end, end)))
        breaks.sort()
        merged = []
        for b in breaks:  # merge overlapping breaks
            if merged and b[0] <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b[1]))
            else:
                merged.append(b)
        return {"start": start, "end": end, "breaks": merged}

    def state(self, now):
        """('active' | 'break' | 'off', until_when)."""
        if not self.cfg.get("enabled", True):
            return "active", None
        today = now.date()
        for day in (today - timedelta(days=1), today):
            p = self.plan(day)
            if p["start"] <= now < p["end"]:
                for a, b in p["breaks"]:
                    if a <= now < b:
                        return "break", b
                return "active", p["end"]
        starts = [self.plan(d)["start"] for d in (today, today + timedelta(days=1))]
        return "off", min(t for t in starts if t > now)
