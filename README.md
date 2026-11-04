# UmaBot

Bot para farmar fãs no **Umamusume: Pretty Derby (Steam)**. Usa o treino independente do jogo, acaba a run,
começa outra e escolhe sempre a carta da **Light Hello**.

Não mexe na API nem no jogo: só tira screenshots da janela e clica com o rato, como uma pessoa.

> ⚠️ Os Termos de Uso da Cygames proíbem ferramentas de automação. Este método é o mais difícil de detetar,
> mas o risco não é zero. Usa por tua conta e risco.

## O que faz para parecer humano

- **Cliques espalhados pelo botão**: cada clique cai num ponto diferente, mais provável perto do centro e nunca nas bordas.
- **Rato em curva**: trajetos de Bézier com aceleração e travagem, velocidade que depende da distância e, às vezes, um pequeno overshoot corrigido.
- **Tempos de reação variáveis** (distribuição log-normal), tempo de carregar no botão variável e pausas curtas ocasionais.
- **Horário normal**: só começa runs dentro de um horário (por omissão 10:30 → 01:00) que muda até ±45 min todos os dias, com 1 a 3 pausas aleatórias. Uma noite inteira parada não desperdiça TP, porque o TP leva 16h40 a encher.
- **Sem TP**: cancela o popup e espera 25–45 min. Nunca gasta itens nem carats.

## Segurança

- Para sozinho se aparecer um popup de carats (`stop_carats`) ou de veteranas cheias.
- Para se o mesmo clique se repetir 25 vezes sem o ecrã mudar.
- Para se não encontrar a Light Hello 8 vezes seguidas.
- Guarda screenshots em `logs/` sempre que para ou não reconhece o ecrã.
- Pode avisar-te no Discord, se meteres um webhook no `config.toml`.
- Para parar à mão: Ctrl+C, ou atira o rato para o canto superior esquerdo do ecrã.

## Instalar

**Opção A: .exe (sem Python).** Vai a *Actions* → última build verde → descarrega `UmaBot-windows`. Descompacta e usa
`UmaBot.exe <comando>` em vez de `python -m umabot <comando>`.

**Opção B: Python 3.11+**
```
pip install -r requirements.txt
```

## Primeira vez

```
python -m umabot baixar       # descarrega os botões genéricos e a carta da Light Hello
python -m umabot verificar    # diz que templates faltam (não precisa do jogo)
python -m umabot recortar X   # recorta cada template em falta, com o jogo aberto
python -m umabot teste        # mostra o que o bot vê agora e o que faria
python -m umabot horario      # mostra o horário dos próximos dias
python -m umabot              # corre o bot
```

A lista do que falta recortar, e onde está cada coisa, está em [`templates/README.md`](templates/README.md).

**Na primeira vez fica a ver** pelo menos uma run completa e uma vez em que o TP acabe, antes de deixar o bot sozinho.

## Requisitos no PC

- Jogo em ecrã inteiro a 1920x1080, no monitor principal, sem janelas por cima.
- O PC não pode bloquear nem suspender (Definições → Energia → nunca). O monitor pode desligar.
- Enquanto o bot corre, o rato é dele.

## Configuração

Está tudo no [`config.toml`](config.toml): horário, tempos, limiares e as **regras**, que dizem o que fazer em cada ecrã.
As regras são avaliadas de cima para baixo e a primeira que bate é executada. Uma regra que use templates que
ainda não existem fica desligada sozinha.

## Estrutura

```
umabot/
  visao.py       janela do jogo, screenshots, procura de templates (multi-escala para a carta)
  humano.py      cliques, trajetos e tempos humanos
  horario.py     horário diário com variação e pausas
  motor.py       motor de regras e ciclo principal
  ferramentas.py baixar, verificar, teste, recortar, posicao, horario
config.toml      configuração e regras
templates/       recortes dos botões
tests/           testes com ecrã sintético (correm no CI)
```
