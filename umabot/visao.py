"""Encontrar a janela do jogo, tirar screenshots e procurar templates.

Os screenshots são normalizados para `resolucao_ref` (ex.: 1920x1080), por isso os
templates funcionam mesmo que a janela tenha outro tamanho (desde que o formato seja igual).
"""
import os
import sys
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Match:
    x: int        # centro, coordenadas reais do ecrã
    y: int
    w: int        # tamanho real no ecrã
    h: int
    score: float


# ---------------------------------------------------------------- janela (Windows)
def _janela_windows(titulo):
    """(left, top, w, h) da área cliente da janela Unity cujo título contém `titulo`."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    achadas = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        if titulo.lower() in buf.value.lower():
            # janelas Unity primeiro (evita apanhar um separador do browser com "Umamusume")
            achadas.append((cls.value != "UnityWndClass", buf.value.lower() != titulo.lower(), hwnd))
        return True

    u32.EnumWindows(cb, 0)
    if not achadas:
        return None
    hwnd = sorted(achadas)[0][2]
    if u32.IsIconic(hwnd):
        return None  # minimizada
    r = wintypes.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(r))
    p = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(p))
    if r.right <= 0 or r.bottom <= 0:
        return None
    return (p.x, p.y, r.right, r.bottom), hwnd


def ativar_dpi():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


# ---------------------------------------------------------------- frames
class Frame:
    """Um screenshot normalizado + cache das procuras feitas nele."""

    def __init__(self, img, rect, visao):
        self.img = img
        self.rect = rect                    # (left, top, w, h) reais
        self.k = rect[2] / img.shape[1]     # px reais por px normalizado
        self.visao = visao
        self._cache = {}
        self._peq = None

    def peq(self):
        if self._peq is None:
            self._peq = reduzir(self.img)
        return self._peq

    def procurar(self, nome):
        if nome not in self._cache:
            self._cache[nome] = self.visao.procurar(self, nome)
        return self._cache[nome]

    def ve(self, nome):
        return self.procurar(nome) is not None

    def para_ecra(self, x, y, w=0, h=0):
        return Match(round(self.rect[0] + x * self.k), round(self.rect[1] + y * self.k),
                     round(w * self.k), round(h * self.k), 1.0)

    def relativo(self, fx, fy):
        """Ponto do ecrã a partir de frações (0-1) da área do jogo."""
        return round(self.rect[0] + fx * self.rect[2]), round(self.rect[1] + fy * self.rect[3])

    def preto(self):
        return float(self.img.mean()) < 3


class Visao:
    def __init__(self, cfg, fonte=None):
        self.cfg = cfg
        self.ref_w = cfg["jogo"]["resolucao_ref"][0]
        self.limiar = cfg["jogo"]["limiar"]
        self.limiares = cfg["jogo"].get("limiares", {})
        self.templates = carregar_templates(cfg["_base"])
        self.fonte = fonte or self._capturar_janela
        self.hwnd = None
        self._sct = None
        self._escala_carta = {}
        self._peqs = {}

    # -- captura
    def _capturar_janela(self):
        if sys.platform != "win32":
            return None
        r = _janela_windows(self.cfg["jogo"]["janela"])
        if r is None:
            return None
        rect, self.hwnd = r
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        mon = {"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]}
        img = cv2.cvtColor(np.array(self._sct.grab(mon)), cv2.COLOR_BGRA2BGR)
        return img, rect

    def capturar(self):
        r = self.fonte()
        if r is None:
            return None
        img, rect = r
        escala = self.ref_w / img.shape[1]
        if abs(escala - 1) > 0.005:
            interp = cv2.INTER_AREA if escala < 1 else cv2.INTER_LINEAR
            img = cv2.resize(img, (self.ref_w, round(img.shape[0] * escala)), interpolation=interp)
        return Frame(img, rect, self)

    def trazer_para_frente(self):
        """Se outra janela estiver por cima do jogo, os cliques iam parar lá."""
        if sys.platform != "win32" or not self.hwnd:
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            if u32.GetForegroundWindow() != self.hwnd:
                u32.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

    # -- procura
    def tem(self, nome):
        return nome in self.templates

    def _peq(self, nome):
        if nome not in self._peqs:
            self._peqs[nome] = reduzir(self.templates[nome])
        return self._peqs[nome]

    def procurar(self, frame, nome, limiar=None):
        val, loc, (h, w) = self._medir(frame, nome)
        limiar = limiar if limiar is not None else self.limiares.get(nome, self.limiar)
        if val < limiar:
            return None
        m = frame.para_ecra(loc[0] + w / 2, loc[1] + h / 2, w, h)
        m.score = val
        return m

    def _medir(self, frame, nome):
        tpl = self.templates.get(nome)
        if tpl is None:
            return 0.0, (0, 0), (0, 0)
        val, loc = procura_rapida(frame.img, frame.peq(), tpl, self._peq(nome))
        return val, loc, tpl.shape[:2]

    def score(self, frame, nome):
        return self._medir(frame, nome)[0] if nome in self.templates else None

    def procurar_carta(self, frame, nomes, escalas, limiar):
        """Procura em vários tamanhos (as imagens da net não têm a escala do jogo).
        Grosseiro a meia resolução em todas as escalas, depois confirma a cores na melhor."""
        melhor = None
        for nome in nomes:
            tpl = self.templates.get(nome)
            if tpl is None:
                continue
            if nome in self._escala_carta:
                s0 = self._escala_carta[nome]
                lista = [s0 * f for f in (1.0, 0.95, 1.05, 0.9, 1.1)]
            else:
                lista = list(np.linspace(escalas[0], escalas[1], 15))
            tp = self._peq(nome)
            for s in lista:
                t = cv2.resize(tp, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
                if min(t.shape[:2]) < 8:
                    continue
                val, loc = semelhanca(frame.peq(), t)
                if melhor is None or val > melhor[0]:
                    melhor = (val, loc, nome, s)
        if melhor is None:
            return None
        _, loc, nome, s = melhor
        tpl = self.templates[nome]
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        val, loc = afinar(frame.img, t, loc[0] * 2, loc[1] * 2)
        if val < limiar:
            return None
        self._escala_carta[nome] = s
        h, w = t.shape[:2]
        m = frame.para_ecra(loc[0] + w / 2, loc[1] + h / 2, w, h)
        m.score = val
        return m


def reduzir(img):
    """Meia resolução em cinzento: ~10x mais rápido para a procura grosseira."""
    return cv2.cvtColor(cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


def afinar(img, tpl, x, y, folga=8):
    """Confirma a cores, à resolução total, só à volta de (x, y)."""
    h, w = tpl.shape[:2]
    x0, y0 = max(0, x - folga), max(0, y - folga)
    roi = img[y0:y0 + h + 2 * folga, x0:x0 + w + 2 * folga]
    val, (dx, dy) = semelhanca(roi, tpl)
    return val, (x0 + dx, y0 + dy)


def procura_rapida(img, img_peq, tpl, tpl_peq):
    if min(tpl_peq.shape[:2]) < 8:  # template muito pequeno: procura normal
        return semelhanca(img, tpl)
    val, (x, y) = semelhanca(img_peq, tpl_peq)
    if val < 0.5:  # nem parecido, não vale a pena confirmar
        return val, (x * 2, y * 2)
    return afinar(img, tpl, x * 2, y * 2)


def semelhanca(img, tpl):
    """(score 0-1, canto superior esquerdo)."""
    if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
        return 0.0, (0, 0)
    if tpl.std() < 3:  # template quase de uma cor só: CCOEFF dá falsos positivos
        res = cv2.matchTemplate(img, tpl, cv2.TM_SQDIFF_NORMED)
        mn, _, loc, _ = cv2.minMaxLoc(res)
        val = 1.0 - mn
    else:
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, val, _, loc = cv2.minMaxLoc(res)
    if not np.isfinite(val):
        return 0.0, (0, 0)
    return float(val), loc


def carregar_templates(base):
    """templates/_baixados/*.png e depois templates/*.png (os teus recortes têm prioridade)."""
    out = {}
    for pasta in (os.path.join(base, "templates", "_baixados"), os.path.join(base, "templates")):
        if not os.path.isdir(pasta):
            continue
        for f in sorted(os.listdir(pasta)):
            if f.lower().endswith(".png"):
                img = cv2.imdecode(np.fromfile(os.path.join(pasta, f), np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    out[f[:-4]] = img
    return out
