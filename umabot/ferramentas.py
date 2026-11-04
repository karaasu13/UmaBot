"""Comandos de apoio: baixar, verificar, teste, recortar, posicao, horario."""
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

import cv2
import numpy as np

from .horario import Horario
from .motor import preparar_regras
from .visao import Visao, carregar_templates

# Botões genéricos do projeto open-source umamusume-auto-train (Steam, 1920x1080),
# fixados num commit para não mudarem. São descarregados para o teu PC, não ficam no repo.
UPSTREAM = ("https://raw.githubusercontent.com/samsulpanjul/umamusume-auto-train/"
            "4b70b185bf1153769e8536e0d6737f0bebc8892c/assets/")
BOTOES = {
    "next": "buttons/next_btn.png",
    "next2": "buttons/next2_btn.png",
    "ok": "buttons/ok_btn.png",
    "ok2": "buttons/ok_2_btn.png",
    "close": "buttons/close_btn.png",
    "cancelar": "buttons/cancel_btn.png",
    "confirmar": "buttons/confirm_btn.png",
    "skip": "buttons/skip_btn.png",
    "skip_grande": "buttons/skip_btn_big.png",
    "complete_career": "buttons/complete_career_btn.png",
    "voltar": "buttons/back_btn.png",
    "lobby_carreira": "ui/tazuna_hint.png",
}
# Ícone da carta SSR Light Hello [From the Ground Up] (GameTora, id 30052)
CARTA_URLS = [
    "https://gametora.com/images/umamusume/supports/support_card_s_30052.png",
    "https://media.gametora.com/umamusume/supports/full/small/30052.png",
]


def _baixar(url, destino):
    req = urllib.request.Request(url, headers={"User-Agent": "UmaBot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        dados = r.read()
    with open(destino, "wb") as f:
        f.write(dados)
    return dados


def baixar(cfg):
    pasta = os.path.join(cfg["_base"], "templates", "_baixados")
    os.makedirs(pasta, exist_ok=True)
    ok = 0
    for nome, rel in BOTOES.items():
        try:
            _baixar(UPSTREAM + rel, os.path.join(pasta, nome + ".png"))
            print(f"  ok  {nome}")
            ok += 1
        except Exception as e:
            print(f"  ERRO {nome}: {e}")
    img, erro = None, None
    for url in CARTA_URLS:
        try:
            dados = _baixar(url, os.path.join(pasta, "carta_lighthello_inteira.png"))
            img = cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                break
        except Exception as e:
            erro = e
    if img is not None:
        h, w = img.shape[:2]
        miolo = img[int(h * 0.12):int(h * 0.70), int(w * 0.15):int(w * 0.85)]  # sem moldura/ícones
        cv2.imencode(".png", miolo)[1].tofile(os.path.join(pasta, "carta_lighthello.png"))
        print(f"  ok  carta_lighthello (+ _inteira), ícone {w}x{h}")
        ok += 1
    else:
        print(f"  ERRO carta Light Hello: {erro}")
        print("       Guarda à mão a imagem da carta de https://gametora.com/umamusume/supports/30052-light-hello")
        print("       como templates/carta_lighthello.png, ou recorta-a da lista de amigos no jogo.")
    print(f"\n{ok} imagens em {pasta}")
    print("Agora corre:  python -m umabot verificar")


def verificar(cfg):
    base = cfg["_base"]
    tpls = carregar_templates(base)
    proprios = {f[:-4] for f in os.listdir(os.path.join(base, "templates")) if f.endswith(".png")} \
        if os.path.isdir(os.path.join(base, "templates")) else set()
    print(f"Templates: {len(tpls)} ({len(proprios)} recortados por ti, {len(tpls) - len(proprios)} baixados)\n")
    regras = preparar_regras(cfg["regra"], lambda n: n in tpls, cfg["carta"]["templates"])
    falta = []
    for r in regras:
        estado = "OK " if r.ativa else "OFF"
        extra = f"  -> faltam: {', '.join(r.em_falta)}" if r.em_falta else ""
        print(f"[{estado}] {r.nome}{extra}")
        falta += [t for t in r.em_falta if t not in falta]
    cartas = cfg["carta"]["templates"]
    if any(t in cartas for t in falta):
        falta = [t for t in falta if t not in cartas]
        print("\nCarta da Light Hello em falta: corre 'baixar', ou recorta-a da lista de amigos com")
        print(f"  python -m umabot recortar {cartas[0]}")
    if falta:
        print("\nPara recortar (com o jogo aberto):")
        for t in falta:
            print(f"  python -m umabot recortar {t}")
    else:
        print("\nNão falta nada!")


def _visao(cfg):
    v = Visao(cfg)
    f = v.capturar()
    if f is None:
        sys.exit("Não encontrei a janela do jogo. Abre o Umamusume (não minimizado) e tenta outra vez.")
    return v, f


def teste(cfg):
    v, f = _visao(cfg)
    print(f"Janela: {f.rect[2]}x{f.rect[3]} em ({f.rect[0]},{f.rect[1]}), normalizado para {f.img.shape[1]}x{f.img.shape[0]}\n")
    anot = f.img.copy()
    linhas = []
    for nome in v.templates:
        if nome in cfg["carta"]["templates"]:
            continue
        val, loc, (h, w) = v._medir(f, nome)
        lim = v.limiares.get(nome, v.limiar)
        linhas.append((val, nome, lim))
        if val >= lim:
            cv2.rectangle(anot, loc, (loc[0] + w, loc[1] + h), (0, 0, 255), 2)
            cv2.putText(anot, nome, (loc[0], max(12, loc[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    for val, nome, lim in sorted(linhas, reverse=True):
        print(f"{'VISTO' if val >= lim else '     '} {val:.3f}  {nome}")

    c = cfg["carta"]
    m = v.procurar_carta(f, c["templates"], c["escalas"], 0.0)
    if m:
        print(f"\nCarta Light Hello: melhor semelhança {m.score:.3f} (limiar {c['limiar']})"
              f"{'  <- ENCONTRADA' if m.score >= c['limiar'] else ''}")

    from .motor import Bot
    bot = Bot(cfg, visao=v, rato=_RatoNulo(), dormir=lambda s: None)
    r, alvo = bot.avaliar(f)
    print(f"\nRegra que o bot executaria agora: {r.nome + ' -> ' + str(alvo) if r else '(nenhuma, ficava à espera)'}")
    p = os.path.join(cfg["_base"], "logs", f"teste_{datetime.now():%Y%m%d_%H%M%S}.png")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cv2.imencode(".png", anot)[1].tofile(p)
    print(f"Screenshot anotado: {p}")


class _RatoNulo:
    def __getattr__(self, _):
        return lambda *a, **k: None


def recortar(cfg, nome, ficheiro=None):
    """Mostra um screenshot do jogo (ou de um ficheiro) e guarda a zona que selecionares."""
    ref_w = cfg["jogo"]["resolucao_ref"][0]
    if ficheiro:
        img = cv2.imdecode(np.fromfile(ficheiro, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            sys.exit(f"Não consegui abrir {ficheiro}")
        if img.shape[1] != ref_w:
            img = cv2.resize(img, (ref_w, round(img.shape[0] * ref_w / img.shape[1])), interpolation=cv2.INTER_AREA)
    else:
        print("Vai para o jogo, screenshot em 3 segundos...")
        time.sleep(3)
        _, f = _visao(cfg)
        img = f.img
    k = min(1.0, 1500 / img.shape[1], 850 / img.shape[0])
    peq = cv2.resize(img, None, fx=k, fy=k) if k < 1 else img
    print("Arrasta um retângulo à volta do botão e carrega ENTER (C cancela).")
    x, y, w, h = cv2.selectROI(f"Recortar: {nome}", peq, showCrosshair=True)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        sys.exit("Cancelado.")
    x, y, w, h = (round(v / k) for v in (x, y, w, h))
    pasta = os.path.join(cfg["_base"], "templates")
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(pasta, nome + ".png")
    cv2.imencode(".png", img[y:y + h, x:x + w])[1].tofile(destino)
    print(f"Guardado {destino} ({w}x{h})")


def posicao(cfg):
    import pyautogui
    v = Visao(cfg)
    print("Mexe o rato por cima do jogo (Ctrl+C para sair).")
    while True:
        f = v.capturar()
        x, y = pyautogui.position()
        if f:
            fx, fy = (x - f.rect[0]) / f.rect[2], (y - f.rect[1]) / f.rect[3]
            print(f"ecrã ({x},{y})  ->  relativo ao jogo [{fx:.2f}, {fy:.2f}]      ", end="\r")
        time.sleep(0.2)


def horario(cfg):
    h = Horario(cfg["horario"])
    hoje = date.today()
    for d in range(3):
        p = h.plano(hoje + timedelta(days=d))
        pausas = ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in p["pausas"]) or "nenhuma"
        print(f"{hoje + timedelta(days=d):%a %d/%m}: {p['inicio']:%H:%M} -> {p['fim']:%H:%M}   pausas: {pausas}")
    est, ate = h.estado(datetime.now())
    print(f"\nAgora: {est}" + (f" até {ate:%H:%M}" if ate else ""))
