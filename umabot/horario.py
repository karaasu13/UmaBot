"""Horário "humano": janela ativa diferente todos os dias + pausas aleatórias.

O plano de cada dia é determinístico (mesma semente + mesma data = mesmo plano),
por isso reiniciar o bot não baralha o dia.
"""
import hashlib
import platform
import random
from datetime import datetime, timedelta


def _hm(texto):
    h, m = texto.split(":")
    return int(h), int(m)


def semente_maquina():
    return int(hashlib.sha256(platform.node().encode()).hexdigest()[:8], 16)


class Horario:
    def __init__(self, cfg_horario, semente=None):
        self.cfg = cfg_horario
        self.semente = semente_maquina() if semente is None else semente

    def plano(self, dia):
        """Devolve dict com inicio, fim (datetime) e pausas [(ini, fim)] do dia `dia` (date)."""
        c = self.cfg
        rng = random.Random(dia.toordinal() * 7919 + self.semente)
        v = c["variacao_min"]
        h, m = _hm(c["inicio"])
        ini_base = datetime(dia.year, dia.month, dia.day, h, m)
        h, m = _hm(c["fim"])
        fim_base = datetime(dia.year, dia.month, dia.day, h, m)
        if fim_base <= ini_base:  # passa da meia-noite
            fim_base += timedelta(days=1)
        ini = ini_base + timedelta(minutes=rng.uniform(-v, v))
        fim = fim_base + timedelta(minutes=rng.uniform(-v, v))

        pausas = []
        n = rng.randint(*c["pausas_por_dia"])
        margem_ini, margem_fim = ini + timedelta(minutes=60), fim - timedelta(minutes=90)
        if margem_fim > margem_ini:
            janela = (margem_fim - margem_ini).total_seconds()
            for _ in range(n):
                p_ini = margem_ini + timedelta(seconds=rng.uniform(0, janela))
                p_fim = p_ini + timedelta(minutes=rng.uniform(*c["duracao_pausa_min"]))
                pausas.append((p_ini, min(p_fim, fim)))
        pausas.sort()
        unidas = []
        for p in pausas:  # junta pausas sobrepostas
            if unidas and p[0] <= unidas[-1][1]:
                unidas[-1] = (unidas[-1][0], max(unidas[-1][1], p[1]))
            else:
                unidas.append(p)
        return {"inicio": ini, "fim": fim, "pausas": unidas}

    def estado(self, agora):
        """('ativo'|'pausa'|'fora', até_quando)."""
        if not self.cfg.get("ativo", True):
            return "ativo", None
        hoje = agora.date()
        for dia in (hoje - timedelta(days=1), hoje):
            p = self.plano(dia)
            if p["inicio"] <= agora < p["fim"]:
                for a, b in p["pausas"]:
                    if a <= agora < b:
                        return "pausa", b
                return "ativo", p["fim"]
        proximos = [self.plano(d)["inicio"] for d in (hoje, hoje + timedelta(days=1))]
        return "fora", min(t for t in proximos if t > agora)
