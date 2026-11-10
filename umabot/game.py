"""Closing and reopening the game, when it's stuck or gone (Windows + Steam)."""
import os
import sys
import time

from .logger import log

STEAM_APP_ID = 3224770  # Umamusume: Pretty Derby (Steam, global)


def _pid_of(hwnd):
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def close(hwnd, wait=20):
    """Asks the game to close, then ends it if it doesn't. True if it's gone."""
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = _pid_of(hwnd)
    u32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    for _ in range(wait * 2):
        time.sleep(0.5)
        if not u32.IsWindow(hwnd):
            return True
    h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)  # PROCESS_TERMINATE | SYNCHRONIZE
    if not h:
        return False
    try:
        k32.TerminateProcess(h, 0)
        k32.WaitForSingleObject(h, 15000)
    finally:
        k32.CloseHandle(h)
    return not u32.IsWindow(hwnd)


def launch(app_id=STEAM_APP_ID):
    """Starts the game through Steam (Steam must be installed; it starts too if it was closed)."""
    if sys.platform != "win32":
        return False
    try:
        os.startfile(f"steam://rungameid/{app_id}")
        return True
    except OSError as e:
        log.info(f"couldn't start the game through Steam ({e})")
        return False


class Game:
    """What the bot uses (a fake one in the tests)."""

    def __init__(self, vision, app_id=STEAM_APP_ID):
        self.vision, self.app_id = vision, app_id

    def restart(self):
        hwnd = getattr(self.vision, "hwnd", None)
        if hwnd:
            close(hwnd)
            time.sleep(3)
        return launch(self.app_id)

    def start(self):
        return launch(self.app_id)
