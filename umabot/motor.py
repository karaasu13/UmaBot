"""Motor de regras: em cada ciclo vê o ecrã, escolhe a primeira regra que bate e executa-a."""
import os
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import cv2

from .horario import Horario
from .humano import Rato, entre
from .registo import log, notificar
from .visao import Visao

ACOES = {"clicar", "parar", "carta", "esperar_tp", "centro"}


@dataclass
class Regra:
    nome: str
    se_vir: list = field(default_factory=list)
    se_nao_vir: list = field(default_factory=list)
    qualquer: list = field(default_factory=list)
    clicar: str | None = None
    acao: str = "clicar"
    inicio_run: bool = False
    conta_run: bool = False
    ativa: bool = True
    em_falta: list = field(default_factory=list)


def preparar_regras(lista, tem, cartas):
    """Cria as regras e desliga as que precisam de templates que ainda não existem."""
    regras = []
    campos = Regra.__dataclass_fields__
    for d in lista:
        r = Regra(**{k: v for k, v in d.items() if k in campos and k not in ("ativa", "em_falta")})
        if r.acao not in ACOES:
            raise ValueError(f"regra '{r.nome}': acao '{r.acao}' não existe (usa {sorted(ACOES)})")
        obrig = list(r.se_vir) + ([r.clicar] if r.clicar else [])
        r.em_falta = [t for t in obrig + r.qualquer + r.se_nao_vir if not tem(t)]
        r.qualquer = [t for t in r.qualquer if tem(t)]
        r.se_nao_vir = [t for t in r.se_nao_vir if tem(t)]
        if any(not tem(t) for t in obrig):
            r.ativa = False
        if not r.se_vir and not d.get("qualquer"):
            raise ValueError(f"regra '{r.nome}': precisa de 'se_vir' ou 'qualquer'")
        if d.get("qualquer") and not r.qualquer:
            r.ativa = False
        if r.acao == "carta" and not any(tem(c) for c in cartas):
            r.ativa = False
            r.em_falta += [c for c in cartas if c not in r.em_falta]
        regras.append(r)
    return regras


class Bot:
    def __init__(self, cfg, visao=None, rato=None, agora=datetime.now, dormir=time.sleep, rng=None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.vis = visao or Visao(cfg)
        self.rato = rato or Rato(cfg["humano"], self.rng, dormir=dormir)
        self.hor = Horario(cfg["horario"])
        self.agora = agora
        self.dormir = dormir
        self.regras = preparar_regras(cfg["regra"], self.vis.tem, cfg["carta"]["templates"])
        self.pasta_logs = os.path.join(cfg["_base"], "logs")
        self.ultimo, self.repet = None, 0
        self.dia, self.runs_hoje = None, 0
        self.sem_regra_desde, self.ja_guardou_desconhecido = None, False
        self.carta_pausa_ate, self.falhas_carta = None, 0
        self.estado_ant, self.sem_janela = None, False

    # ------------------------------------------------------------ utilitários
    def webhook(self):
        return self.cfg["seguranca"]["discord_webhook"]

    def guardar(self, frame, tag):
        if frame is None:
            return None
        os.makedirs(self.pasta_logs, exist_ok=True)
        p = os.path.join(self.pasta_logs, f"{tag}_{self.agora():%Y%m%d_%H%M%S}.png")
        ok, buf = cv2.imencode(".png", frame.img)
        if ok:
            buf.tofile(p)
        return p

    def resumo(self):
        for r in self.regras:
            if r.ativa:
                extra = f" (ignorados por faltarem: {', '.join(r.em_falta)})" if r.em_falta else ""
                log.info(f"  [ok]  {r.nome}{extra}")
            else:
                log.info(f"  [off] {r.nome} -> faltam: {', '.join(r.em_falta)}")

    # ------------------------------------------------------------ decisão
    def avaliar(self, frame, bloquear_inicio=False):
        agora = self.agora()
        for r in self.regras:
            if not r.ativa or (bloquear_inicio and r.inicio_run):
                continue
            if r.acao == "carta" and self.carta_pausa_ate and agora < self.carta_pausa_ate:
                continue
            if any(not frame.ve(t) for t in r.se_vir):
                continue
            if any(frame.ve(t) for t in r.se_nao_vir):
                continue
            if r.qualquer:
                alvo = next((t for t in r.qualquer if frame.ve(t)), None)
                if alvo is None:
                    continue
            else:
                alvo = r.clicar or r.se_vir[0]
            return r, alvo
        return None, None

    def passo(self):
        """Um ciclo. Devolve quantos segundos dormir, ou None para parar."""
        agora = self.agora()
        if agora.date() != self.dia:
            if self.dia is not None:
                log.info(f"dia {self.dia}: {self.runs_hoje} runs")
            self.dia, self.runs_hoje = agora.date(), 0

        est, ate = self.hor.estado(agora)
        if est != self.estado_ant:
            txt = {"ativo": "a jogar", "pausa": "pausa", "fora": "fora de horas"}[est]
            log.info(f"-- {txt}" + (f" até {ate:%d/%m %H:%M}" if ate else ""))
            self.estado_ant = est
        if est == "pausa" or (est == "fora" and not self.cfg["horario"]["acabar_run_fora_de_horas"]):
            return min(300.0, max(5.0, (ate - agora).total_seconds()))

        frame = self.vis.capturar()
        if frame is None:
            if not self.sem_janela:
                log.info("janela do jogo não encontrada (aberta? minimizada?)")
                self.sem_janela = True
            return 15
        self.sem_janela = False
        if frame.preto():
            log.info("ecrã preto: o PC bloqueou ou o jogo está minimizado?")
            return 30

        r, alvo = self.avaliar(frame, bloquear_inicio=(est != "ativo"))
        if r is None:
            self.ultimo, self.repet = None, 0
            self._ecra_desconhecido(frame, agora)
            return entre(self.rng, (60, 120)) if est == "fora" else entre(self.rng, self.cfg["humano"]["intervalo_s"])
        self.sem_regra_desde, self.ja_guardou_desconhecido = None, False

        chave = (r.nome, alvo)
        self.repet = self.repet + 1 if chave == self.ultimo else 1
        self.ultimo = chave
        if r.acao != "carta" and self.repet > self.cfg["seguranca"]["max_repeticoes"]:
            p = self.guardar(frame, "encravado")
            notificar(self.webhook(), f"'{r.nome}' repetiu {self.repet - 1}x sem o ecrã mudar. Parei. ({p})")
            return None
        return self.executar(r, alvo, frame)

    def _ecra_desconhecido(self, frame, agora):
        if self.sem_regra_desde is None:
            self.sem_regra_desde = agora
            return
        mins = (agora - self.sem_regra_desde).total_seconds() / 60
        if not self.ja_guardou_desconhecido and mins >= self.cfg["seguranca"]["desconhecido_min"]:
            p = self.guardar(frame, "desconhecido")
            log.info(f"há {mins:.0f} min sem reconhecer o ecrã, screenshot em {p}")
            self.ja_guardou_desconhecido = True

    # ------------------------------------------------------------ ações
    def executar(self, r, alvo, frame):
        if r.acao == "parar":
            p = self.guardar(frame, "stop")
            notificar(self.webhook(), f"parei: '{r.nome}' ({p})")
            return None
        if r.acao == "carta":
            return self.escolher_carta()

        self.rato.reagir()
        frame = self.vis.capturar()  # o ecrã pode ter mudado durante a "reação"
        if frame is None:
            return 2
        if r.acao == "centro":
            x, y = frame.relativo(0.5, 0.62)
            w, h = round(frame.rect[2] * 0.25), round(frame.rect[3] * 0.12)
        else:
            m = frame.procurar(alvo)
            if m is None:
                return 0.5
            x, y, w, h = m.x, m.y, m.w, m.h
        log.info(f"[{r.nome}] clique em '{alvo}'")
        self.vis.trazer_para_frente()
        self.rato.clicar(x, y, w, h)
        if r.conta_run:
            self.runs_hoje += 1
            log.info(f"run nº {self.runs_hoje} de hoje começou")
        if r.acao == "esperar_tp":
            mins = entre(self.rng, self.cfg["tp"]["espera_min"])
            log.info(f"sem TP, volto a tentar daqui a {mins:.0f} min")
            return mins * 60
        self.rato.talvez_distrair(log.info)
        return entre(self.rng, self.cfg["humano"]["intervalo_s"])

    def escolher_carta(self):
        c = self.cfg["carta"]
        frame = None
        for i in range(c["scroll_tentativas"] + 1):
            frame = self.vis.capturar()
            if frame is None:
                return 5
            m = self.vis.procurar_carta(frame, c["templates"], c["escalas"], c["limiar"])
            if m:
                self.rato.reagir()
                log.info(f"Light Hello encontrada (semelhança {m.score:.2f})")
                self.rato.clicar(m.x, m.y, m.w, m.h)
                self.falhas_carta = 0
                self.carta_pausa_ate = self.agora() + timedelta(seconds=20)
                return entre(self.rng, (1.5, 2.5))
            if i < c["scroll_tentativas"]:
                self.rato.scroll(*frame.relativo(*c["scroll_pos"]), c["scroll_qtd"])
                self.dormir(self.rng.uniform(0.8, 1.4))

        self.falhas_carta += 1
        log.info(f"não encontrei a Light Hello ({self.falhas_carta}/{c['max_falhas']})")
        if self.falhas_carta >= c["max_falhas"]:
            p = self.guardar(frame, "sem_carta")
            notificar(self.webhook(), f"não encontrei a Light Hello {self.falhas_carta}x seguidas. Parei. ({p})")
            return None
        frame = self.vis.capturar()
        m = frame.procurar(c["atualizar"]) if frame is not None and c.get("atualizar") else None
        if m:
            self.rato.reagir()
            log.info("a atualizar a lista de amigos")
            self.rato.clicar(m.x, m.y, m.w, m.h)
        elif frame is not None:
            self.rato.scroll(*frame.relativo(*c["scroll_pos"]), -c["scroll_qtd"] * (c["scroll_tentativas"] + 2))
        return entre(self.rng, (20, 40))

    # ------------------------------------------------------------ ciclo principal
    def correr(self):
        log.info(f"UmaBot a arrancar. {len(self.vis.templates)} templates carregados. Regras:")
        self.resumo()
        log.info("Para parar: Ctrl+C, ou atira o rato para o canto superior esquerdo do ecrã.")
        erros = 0
        while True:
            try:
                espera = self.passo()
                erros = 0
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    raise
                erros += 1
                log.info("erro:\n" + traceback.format_exc())
                if erros >= 20:
                    notificar(self.webhook(), "20 erros seguidos, parei. Vê logs/umabot.log")
                    return
                espera = 10
            if espera is None:
                return
            self.dormir(espera)
