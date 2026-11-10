"""Reading numbers and rows off the words on screen (TP, the cost of a run, item rows)."""
import re


def _keys(words):
    return [re.sub(r"\s+", "", w.text.lower()) for w in words]


def spend_tp(words):
    """'Spend 30 TP to begin training?' -> 30 (60 with the Event Boost)."""
    for k in _keys(words):
        m = re.search(r"spend(\d+)tp", k)
        if m:
            return int(m.group(1))
    return None


def need_tp(words):
    """'You need 9 more TP to start a Career playthrough.' -> 9."""
    for k in _keys(words):
        m = re.search(r"need(\d+)moretp", k)
        if m:
            return int(m.group(1))
    return None


def tp_now(words):
    """The TP shown on screen: '100/100' next to 'TP' (home) or 'TP 100 > 70' (Final Confirmation, where it's
    often read as one number, '10070': split so that the difference is the cost)."""
    keys = _keys(words)
    cost = spend_tp(words)
    if cost:
        for k in keys:
            if k.isdigit() and 3 <= len(k) <= 6:
                for i in range(1, len(k)):
                    a, b = int(k[:i]), int(k[i:])
                    if a - b == cost and not k[i:].startswith("0"):
                        return a
    for i, k in enumerate(keys):
        m = re.fullmatch(r"(?:tp)?(\d{1,3})/(\d{2,3})", k)
        if m and any("tp" in keys[j] for j in range(max(0, i - 2), i + 1)):
            return int(m.group(1))
    for i, k in enumerate(keys):
        if k == "tp":
            for nxt in keys[i + 1:i + 3]:
                if nxt.isdigit():
                    return int(nxt)
    return None


def row_button(words, row_pattern, button_pattern):
    """The button (e.g. 'Use') on the same row as the text matching row_pattern (e.g. 'Toughness 30')."""
    rows = [w for w in words if re.search(row_pattern, w.key)]
    if not rows:
        return None, None
    row = rows[0]
    cy = row.center[1]
    buttons = [w for w in words if re.fullmatch(button_pattern, w.key) and w.box[0] > row.box[0]
               and abs(w.center[1] - cy) < max(30, row.box[3] * 2.5)]
    return row, (min(buttons, key=lambda w: abs(w.center[1] - cy)) if buttons else None)


def held(words, row_pattern):
    """How many of an item are held: the number after 'Held' on that item's row."""
    row, _ = row_button(words, row_pattern, r"x^")
    if row is None:
        return None
    nums = [w for w in words if re.fullmatch(r"[\d,]+", w.text.strip()) and w.box[1] > row.box[1]
            and w.box[1] - row.box[1] < row.box[3] * 3 and w.box[0] > row.box[0]]
    if not nums:
        return None
    return int(min(nums, key=lambda w: w.box[1]).text.replace(",", ""))
