"""Testes sem jogo nem ecrã: ecrã sintético, rato falso, relógio falso."""
import random
from datetime import date, datetime, timedelta

import cv2
import numpy as np
import pytest

from umabot import config
from umabot.horario import Horario
from umabot.humano import Rato, caminho, ponto_no_botao, tempo_reacao
from umabot.motor import Bot, preparar_regras
from umabot.visao import Visao

REF = (1920, 1080)


def ecra_sintetico(seed=0):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (REF[1], REF[0], 3), dtype=np.uint8)
    return cv2.GaussianBlur(img, (0, 0), 3)  # textura que sobrevive a redimensionar


class RatoFalso:
    def __init__(self):
        self.cliques, self.scrolls = [], []

    def clicar(self, x, y, w, h):
        self.cliques.append((x, y, w, h))

    def scroll(self, x, y, q):
        self.scrolls.append((x, y, q))

    def reagir(self):
        pass

    def talvez_distrair(self, log=None):
        pass


@pytest.fixture
def mundo(tmp_path):
    """Ecrã sintético + templates recortados dele (e alguns que não estão no ecrã)."""
    ecra = ecra_sintetico()
    outro = ecra_sintetico(99)
    t = tmp_path / "templates"
    t.mkdir()
    rects = {
        "next": (200, 900, 220, 60),
        "home_carreira": (1500, 950, 180, 80),
        "ecra_amigos": (50, 40, 300, 50),
    }
    for nome, (x, y, w, h) in rects.items():
        cv2.imwrite(str(t / f"{nome}.png"), ecra[y:y + h, x:x + w])
    for nome in ("stop_carats", "popup_tp", "cancelar"):
        cv2.imwrite(str(t / f"{nome}.png"), outro[100:160, 100:300])  # não está no ecrã
    # carta: no ecrã aparece a 60% do tamanho do "ícone da net"
    carta_ecra = (700, 400, 120, 150)
    x, y, w, h = carta_ecra
    icone = cv2.resize(ecra[y:y + h, x:x + w], None, fx=1 / 0.6, fy=1 / 0.6, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(t / "carta_lighthello.png"), icone)

    cfg = config.carregar(base=str(tmp_path))
    cfg["regra"] = [
        {"nome": "stop", "se_vir": ["stop_carats"], "acao": "parar"},
        {"nome": "tp", "se_vir": ["popup_tp"], "clicar": "cancelar", "acao": "esperar_tp"},
        {"nome": "falta", "se_vir": ["nao_existe"]},
        {"nome": "inicio", "se_vir": ["home_carreira"], "inicio_run": True, "conta_run": True},
        {"nome": "generico", "qualquer": ["next", "ok_inexistente"]},
    ]
    cfg["horario"]["ativo"] = False
    estado = {"img": ecra}
    fonte = lambda: (estado["img"], (0, 0, estado["img"].shape[1], estado["img"].shape[0]))
    return cfg, Visao(cfg, fonte=fonte), estado, rects, carta_ecra


def fazer_bot(cfg, vis, agora=None):
    rato = RatoFalso()
    agora = agora or (lambda: datetime(2026, 11, 4, 15, 0))
    return Bot(cfg, visao=vis, rato=rato, agora=agora, dormir=lambda s: None, rng=random.Random(1)), rato


def dentro(clique, rect):
    x, y, w, h = rect
    return x <= clique[0] <= x + w and y <= clique[1] <= y + h


# ---------------------------------------------------------------- regras
def test_regras_sem_templates_ficam_desligadas(mundo):
    cfg, vis, *_ = mundo
    regras = {r.nome: r for r in preparar_regras(cfg["regra"], vis.tem, cfg["carta"]["templates"])}
    assert not regras["falta"].ativa and regras["falta"].em_falta == ["nao_existe"]
    assert regras["generico"].ativa and regras["generico"].qualquer == ["next"]


def test_acao_invalida_rebenta(mundo):
    cfg, vis, *_ = mundo
    with pytest.raises(ValueError):
        preparar_regras([{"nome": "x", "se_vir": ["next"], "acao": "voar"}], vis.tem, [])


def test_clica_dentro_do_botao_e_conta_run(mundo):
    cfg, vis, _, rects, _ = mundo
    bot, rato = fazer_bot(cfg, vis)
    assert bot.passo() is not None
    assert dentro(rato.cliques[-1], rects["home_carreira"])  # "inicio" vem antes do genérico
    assert bot.runs_hoje == 1


def test_stop_tem_prioridade(mundo):
    cfg, vis, estado, *_ = mundo
    ecra = estado["img"].copy()
    stop = cv2.imread(str(vis.cfg["_base"] + "/templates/stop_carats.png"))
    ecra[500:560, 900:1100] = stop
    estado["img"] = ecra
    vis.templates = vis.templates  # mesmos templates
    bot, rato = fazer_bot(cfg, vis)
    assert bot.passo() is None and rato.cliques == []


def test_fora_de_horas_nao_comeca_run_mas_acaba_a_atual(mundo):
    cfg, vis, _, rects, _ = mundo
    cfg["horario"].update(ativo=True, inicio="10:00", fim="12:00", variacao_min=0, pausas_por_dia=[0, 0])
    bot, rato = fazer_bot(cfg, vis, agora=lambda: datetime(2026, 11, 4, 20, 0))
    bot.passo()
    assert dentro(rato.cliques[-1], rects["next"])  # saltou o "inicio" e clicou o genérico
    assert bot.runs_hoje == 0


def test_pausa_nao_tira_screenshots(mundo):
    cfg, vis, *_ = mundo
    cfg["horario"].update(ativo=True, inicio="10:00", fim="12:00", variacao_min=0,
                          acabar_run_fora_de_horas=False, pausas_por_dia=[0, 0])
    vis.fonte = lambda: pytest.fail("não devia capturar")
    bot, rato = fazer_bot(cfg, vis, agora=lambda: datetime(2026, 11, 4, 20, 0))
    espera = bot.passo()
    assert 5 <= espera <= 300 and rato.cliques == []


def test_encravado_para(mundo):
    cfg, vis, *_ = mundo
    cfg["seguranca"]["max_repeticoes"] = 3
    bot, _ = fazer_bot(cfg, vis)
    resultados = [bot.passo() for _ in range(5)]
    assert None in resultados


def test_carta_em_escala_diferente(mundo):
    cfg, vis, _, _, carta_ecra = mundo
    cfg["carta"]["templates"] = ["carta_lighthello"]
    bot, rato = fazer_bot(cfg, vis)
    bot.escolher_carta()
    assert rato.cliques and dentro(rato.cliques[-1], carta_ecra)
    assert rato.scrolls == []
    assert abs(vis._escala_carta["carta_lighthello"] - 0.6) < 0.08


def test_carta_ausente_faz_scroll_e_conta_falha(mundo):
    cfg, vis, estado, *_ = mundo
    cfg["carta"]["templates"] = ["carta_lighthello"]
    estado["img"] = ecra_sintetico(5)
    bot, rato = fazer_bot(cfg, vis)
    assert bot.escolher_carta() is not None
    assert len(rato.scrolls) >= cfg["carta"]["scroll_tentativas"] and bot.falhas_carta == 1


# ---------------------------------------------------------------- visão
def test_normaliza_janela_mais_pequena(mundo):
    cfg, vis, estado, rects, _ = mundo
    estado["img"] = cv2.resize(estado["img"], (960, 540), interpolation=cv2.INTER_AREA)  # janela a metade
    f = vis.capturar()
    m = f.procurar("next")
    x, y, w, h = rects["next"]
    assert m and abs(m.x - (x + w / 2) / 2) <= 2 and abs(m.y - (y + h / 2) / 2) <= 2
    assert abs(m.w - w / 2) <= 1


# ---------------------------------------------------------------- humano
def test_ponto_no_botao_fica_dentro_e_varia():
    rng = random.Random(0)
    pts = [ponto_no_botao(500, 300, 200, 60, rng) for _ in range(5000)]
    xs, ys = zip(*pts)
    assert min(xs) >= 500 - 76 and max(xs) <= 500 + 76
    assert min(ys) >= 300 - 23 and max(ys) <= 300 + 23
    assert np.std(xs) > 20 and len(set(pts)) > 1000


def test_caminho_acaba_no_alvo_e_curva():
    rng = random.Random(3)
    for _ in range(50):
        pts = caminho(0, 0, 800, 400, rng)
        assert pts[-1] == (800, 400) and len(pts) >= 12


def test_tempo_reacao_no_intervalo():
    rng = random.Random(0)
    v = [tempo_reacao(rng, (0.35, 1.6)) for _ in range(2000)]
    assert min(v) >= 0.35 and max(v) <= 1.6 and np.std(v) > 0.1


def test_rato_com_backend_falso():
    class PG:
        def __init__(self):
            self.pos, self.eventos = (0, 0), []

        def position(self):
            return self.pos

        def moveTo(self, x, y):
            self.pos = (x, y)

        def mouseDown(self):
            self.eventos.append(("down", self.pos))

        def mouseUp(self):
            self.eventos.append(("up", self.pos))

        def scroll(self, q):
            self.eventos.append(("scroll", q))

    pg = PG()
    cfg = config.PADRAO["humano"]
    r = Rato(cfg, random.Random(0), backend=pg, dormir=lambda s: None)
    x, y = r.clicar(1000, 500, 200, 60)
    assert pg.eventos[-2] == ("down", (x, y)) and pg.eventos[-1] == ("up", (x, y))


# ---------------------------------------------------------------- horário
def test_horario_passa_meia_noite_e_e_deterministico():
    c = dict(config.PADRAO["horario"], inicio="10:30", fim="01:00", variacao_min=45)
    h = Horario(c, semente=42)
    p = h.plano(date(2026, 11, 4))
    assert p == h.plano(date(2026, 11, 4))
    assert p["fim"].date() == date(2026, 11, 5)
    assert timedelta(hours=13) <= p["fim"] - p["inicio"] <= timedelta(hours=16)
    for a, b in p["pausas"]:
        assert p["inicio"] < a < b <= p["fim"]
    assert h.estado(datetime(2026, 11, 5, 0, 0))[0] in ("ativo", "pausa")  # ainda é o dia 4
    assert h.estado(datetime(2026, 11, 5, 4, 0))[0] == "fora"


def test_horario_muda_de_dia_para_dia():
    h = Horario(dict(config.PADRAO["horario"]), semente=7)
    inicios = {h.plano(date(2026, 11, d))["inicio"].time() for d in range(1, 15)}
    assert len(inicios) > 10


def test_config_do_repo_e_valida():
    cfg = config.carregar()
    preparar_regras(cfg["regra"], lambda n: True, cfg["carta"]["templates"])
    assert len(cfg["regra"]) >= 10
