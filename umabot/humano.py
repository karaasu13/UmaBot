"""Tudo o que faz o rato parecer uma pessoa: ponto do clique, trajeto curvo, tempos."""
import math
import random
import time


def entre(rng, par):
    return rng.uniform(par[0], par[1])


def ponto_no_botao(cx, cy, w, h, rng=random):
    """Ponto aleatório dentro do botão: mais provável perto do centro,
    mas espalhado por ~75% da área (nunca nas bordas)."""
    def amostra(meio, tam):
        lim = tam * 0.38
        while True:
            d = rng.gauss(0, tam * 0.17)
            if abs(d) <= lim:
                return meio + d
    return round(amostra(cx, w)), round(amostra(cy, h))


def tempo_reacao(rng, par):
    """Distribuição log-normal (como tempos de reação reais), cortada ao intervalo."""
    lo, hi = par
    mu = math.log(max(1e-3, (lo + hi) / 2.6))
    return min(hi, max(lo, rng.lognormvariate(mu, 0.45)))


def caminho(x0, y0, x1, y1, rng=random):
    """Trajeto em curva de Bézier com aceleração/travagem e overshoot ocasional."""
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 3:
        return [(x1, y1)]
    n = max(12, min(70, int(dist / 14)))
    px, py = -(y1 - y0) / dist, (x1 - x0) / dist  # perpendicular
    d1 = rng.uniform(-0.22, 0.22) * dist
    d2 = d1 * rng.uniform(0.1, 0.9)
    t1, t2 = rng.uniform(0.2, 0.45), rng.uniform(0.55, 0.85)
    c1 = (x0 + (x1 - x0) * t1 + px * d1, y0 + (y1 - y0) * t1 + py * d1)
    c2 = (x0 + (x1 - x0) * t2 + px * d2, y0 + (y1 - y0) * t2 + py * d2)

    pts = []
    for i in range(1, n + 1):
        t = i / n
        t = t * t * (3 - 2 * t)  # ease-in-out
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        x = a * x0 + b * c1[0] + c * c2[0] + d * x1
        y = a * y0 + b * c1[1] + c * c2[1] + d * y1
        pts.append((round(x), round(y)))

    if dist > 150 and rng.random() < 0.15:  # passa um bocadinho e corrige
        ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
        o = rng.uniform(3, 9)
        pts[-1:] = [(round(x1 + ux * o), round(y1 + uy * o)), (x1, y1)]
    pts[-1] = (x1, y1)
    return pts


class Rato:
    """Envolve o pyautogui com movimentos humanos. `backend` permite testes sem ecrã."""

    def __init__(self, cfg_humano, rng=None, backend=None, dormir=time.sleep):
        self.cfg = cfg_humano
        self.rng = rng or random.Random()
        self.dormir = dormir
        if backend is None:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0
            backend = pyautogui
        self.pg = backend

    def mover(self, x, y):
        x0, y0 = self.pg.position()
        pts = caminho(x0, y0, x, y, self.rng)
        dist = math.hypot(x - x0, y - y0)
        dur = self.rng.uniform(0.12, 0.22) + 0.00035 * dist + abs(self.rng.gauss(0, 0.04))
        passo = dur / len(pts)
        for px, py in pts:
            self.pg.moveTo(px, py)
            self.dormir(passo)

    def clicar(self, cx, cy, w, h):
        x, y = ponto_no_botao(cx, cy, w, h, self.rng)
        self.mover(x, y)
        self.dormir(self.rng.uniform(0.03, 0.12))
        self.pg.mouseDown()
        self.dormir(entre(self.rng, self.cfg["segurar_ms"]) / 1000)
        self.pg.mouseUp()
        return x, y

    def scroll(self, x, y, qtd):
        self.mover(x + self.rng.randint(-15, 15), y + self.rng.randint(-15, 15))
        passo = -1 if qtd < 0 else 1
        for _ in range(abs(qtd)):  # vários "cliques" de roda, com ritmo irregular
            self.pg.scroll(passo * self.rng.randint(1, 2))
            self.dormir(self.rng.uniform(0.03, 0.11))

    def reagir(self):
        """Espera antes de clicar, como alguém que acabou de ver o botão."""
        self.dormir(tempo_reacao(self.rng, self.cfg["reacao_s"]))

    def talvez_distrair(self, log=None):
        if self.rng.random() < self.cfg["distracao_prob"]:
            s = entre(self.rng, self.cfg["distracao_s"])
            if log:
                log(f"pausa curta de {s:.0f}s")
            self.dormir(s)
