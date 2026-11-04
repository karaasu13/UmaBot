"""python -m umabot [comando]"""
import argparse
import sys

from . import config, ferramentas
from .registo import configurar
from .visao import ativar_dpi


def main(argv=None):
    p = argparse.ArgumentParser(prog="umabot", description="Bot de fãs para Umamusume (Steam).")
    p.add_argument("--config", help="caminho do config.toml")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("correr", help="corre o bot (é o que faz sem comando)")
    sub.add_parser("baixar", help="descarrega os botões genéricos e a carta da Light Hello")
    sub.add_parser("verificar", help="mostra que templates faltam (não precisa do jogo)")
    sub.add_parser("teste", help="screenshot do jogo + o que o bot vê e faria agora")
    r = sub.add_parser("recortar", help="recorta um template do jogo ou de um screenshot")
    r.add_argument("nome")
    r.add_argument("--de", help="recortar de um ficheiro PNG em vez do jogo")
    sub.add_parser("posicao", help="mostra a posição do rato relativa ao jogo")
    sub.add_parser("horario", help="mostra o horário dos próximos dias")
    a = p.parse_args(argv)

    ativar_dpi()
    cfg = config.carregar(a.config)
    cmd = a.cmd or "correr"
    if cmd == "baixar":
        ferramentas.baixar(cfg)
    elif cmd == "verificar":
        ferramentas.verificar(cfg)
    elif cmd == "teste":
        ferramentas.teste(cfg)
    elif cmd == "recortar":
        ferramentas.recortar(cfg, a.nome, a.de)
    elif cmd == "posicao":
        ferramentas.posicao(cfg)
    elif cmd == "horario":
        ferramentas.horario(cfg)
    else:
        from .motor import Bot
        configurar(cfg["_base"])
        try:
            Bot(cfg).correr()
        except KeyboardInterrupt:
            print("\nparado.")
        except Exception as e:
            if type(e).__name__ == "FailSafeException":
                print("\nparado (rato no canto do ecrã).")
            else:
                raise


if __name__ == "__main__":
    sys.exit(main())
