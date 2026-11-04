# Templates

Cada template é um recorte PNG de um botão (ou de algo que identifica um ecrã).
O nome do ficheiro tem de ser **exatamente** o da tabela, porque é o nome que as regras do `config.toml` usam.

- `templates/_baixados/` é criada pelo `python -m umabot baixar`, não vai para o git.
- `templates/*.png` são os teus recortes. Se tiverem o mesmo nome que um dos baixados, ganham os teus.

Recorta sempre com o jogo em **ecrã inteiro a 1920x1080**. Noutro tamanho com o mesmo formato
(1600x900, 1280x720, ...) o bot também ajusta, mas a 1920x1080 é mais fiável.

## Já vêm do `baixar`

| Nome | O que é |
|---|---|
| `next`, `next2` | botão "Next" |
| `ok` | botão "OK" |
| `close` | botão "Close" |
| `cancelar` | botão "Cancel" (usado no popup de TP) |
| `skip`, `skip_grande` | botão ⏭ de saltar |
| `complete_career` | botão rosa "Complete Career" |
| `confirmar`, `voltar`, `ok2`, `lobby_carreira` | extra, por agora não são usados por nenhuma regra |
| `carta_lighthello`, `carta_lighthello_inteira` | ícone da SSR Light Hello [From the Ground Up], tirado do GameTora |

> Os botões vêm do projeto [umamusume-auto-train](https://github.com/samsulpanjul/umamusume-auto-train)
> (versão Steam, 1920x1080) e a carta vem do [GameTora](https://gametora.com/umamusume/supports/30052-light-hello).
> São descarregados para o teu PC, não ficam guardados neste repositório.

## Falta recortar (precisa do jogo)

| Nome | Onde aparece | Para quê |
|---|---|---|
| `home_carreira` | menu principal, botão **Career** | começar uma run (só dentro do horário) |
| `slot_amigo_vazio` | ecrã do deck, o espaço vazio da carta de amigo | abrir a lista de amigos |
| `ecra_amigos` | lista de cartas de amigos: algo que só existe nesse ecrã (ex.: o título) | saber quando procurar a Light Hello |
| `atualizar` | lista de amigos, botão de atualizar (opcional) | se a Light Hello não aparecer |
| `iniciar_carreira` | ecrã do deck, botão **Start Career!** | |
| `confirmar_inicio` | popup a confirmar o início (o que mostra os 30 TP) | conta as runs |
| `treino_independente` | botão que liga o treino independente | |
| `confirmar_treino_independente` | confirmação do treino independente (se existir) | |
| `popup_tp` | popup de TP insuficiente: algo que só existe nele (ex.: o título) | cancela e espera |
| `stop_carats` | qualquer popup que vá **gastar carats** | **para o bot**: muito importante |
| `stop_veteranas_cheias` | aviso de lista de veteranas cheia | para o bot |
| `ecra_titulo` | ecrã inicial ("Tap to start") | clica no meio |
| `erro_ligacao` + `tentar_novamente` | popup de erro de ligação e o botão **Retry** | |

### Como recortar

```
python -m umabot recortar home_carreira
```
Tira um screenshot do jogo daqui a 3 segundos. Arrasta um retângulo à volta do botão e carrega ENTER.

Recorta só o botão, com uma margem pequena. Evita apanhar números que mudam (TP, fãs, datas) ou a personagem.

### Alternativa: mandar screenshots inteiros

Se for mais fácil, tira screenshots do ecrã inteiro (tecla PrtScn, com o jogo em 1920x1080) de cada ecrã
da tabela. Mete-os em `capturas/` com o nome do template (ex.: `capturas/home_carreira.png`) e faz push.
Os recortes faço-os eu a partir daí, ou fazes tu com:

```
python -m umabot recortar home_carreira --de capturas/home_carreira.png
```
