"""Leitura do config.toml com valores por omissão."""
import copy
import os
import sys
import tomllib

PADRAO = {
    "jogo": {
        "janela": "Umamusume",
        "resolucao_ref": [1920, 1080],
        "limiar": 0.85,
        "limiares": {},
    },
    "horario": {
        "ativo": True,
        "inicio": "10:30",
        "fim": "01:00",
        "variacao_min": 45,
        "pausas_por_dia": [1, 3],
        "duracao_pausa_min": [15, 70],
        "acabar_run_fora_de_horas": True,
    },
    "humano": {
        "reacao_s": [0.35, 1.6],
        "segurar_ms": [55, 140],
        "distracao_prob": 0.04,
        "distracao_s": [6, 75],
        "intervalo_s": [0.8, 1.8],
    },
    "tp": {"espera_min": [25, 45]},
    "carta": {
        "templates": ["carta_lighthello", "carta_lighthello_inteira"],
        "limiar": 0.75,
        "escalas": [0.4, 1.8],
        "scroll_pos": [0.30, 0.55],
        "scroll_qtd": -5,
        "scroll_tentativas": 6,
        "atualizar": "atualizar",
        "max_falhas": 8,
    },
    "seguranca": {
        "max_repeticoes": 25,
        "desconhecido_min": 40,
        "discord_webhook": "",
    },
    "regra": [],
}


def pasta_base():
    """Pasta do projeto (ou do .exe, se for a versão compilada)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fundir(base, novo):
    for k, v in novo.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _fundir(base[k], v)
        else:
            base[k] = v
    return base


def carregar(caminho=None, base=None):
    base = base or pasta_base()
    caminho = caminho or os.path.join(base, "config.toml")
    dados = {}
    if os.path.exists(caminho):
        with open(caminho, "rb") as f:
            dados = tomllib.load(f)
    cfg = _fundir(copy.deepcopy(PADRAO), dados)
    cfg["_base"] = base
    return cfg
