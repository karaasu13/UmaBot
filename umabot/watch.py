"""Stops the bot as soon as the person uses the mouse or the keyboard."""
import sys
import threading
import time


def _last_input_tick():
    """Windows: time of the last mouse/keyboard input from any source (ms)."""
    if sys.platform != "win32":
        return None
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


class InputWatch:
    """Any input while UmaBot isn't moving the mouse itself counts as the person taking over."""

    def __init__(self, mouse, on_user, position=None, grace=3.0, interval=0.12):
        self.mouse, self.on_user = mouse, on_user
        self.position = position or (lambda: mouse.pg.position())
        self.grace, self.interval = grace, interval
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _signature(self):
        return _last_input_tick(), tuple(self.position())

    def _loop(self):
        if self._stop.wait(self.grace):  # time to let go of the mouse after pressing Start
            return
        base = self._signature()
        while not self._stop.wait(self.interval):
            if self.mouse.busy or time.monotonic() - self.mouse.done_at < 0.35:
                base = self._signature()  # UmaBot's own movement
                continue
            if self._signature() != base:
                self.on_user()
                return
