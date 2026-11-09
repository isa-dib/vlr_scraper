# Coleta de dados do vlr.gg — TCC (predição de vitória por round em Valorant)

Scraper das abas **2D Replay** e **Economy** do vlr.gg. Gera uma tabela round a round
com economia, tipo de compra, kills, lado e resultado dos dois times, além de um
relatório de cobertura que separa **mapa não jogado** de **mapa sem replay**.

Feito para rodar no **Google Colab**, gravando tudo numa pasta do **Google Drive**
(as tabelas finais em **Parquet**; as de amostra em CSV). Também roda localmente
pela linha de comando.

## Sumário

1. [Uso no Google Colab](#uso-no-google-colab)
2. [Uso local (linha de comando)](#uso-local-linha-de-comando)
   - [VS Code com dados já coletados](#vs-code-com-dados-já-coletados)
3. [Arquivos gerados](#arquivos-gerados)
   - [Análise e limpeza (etapa de transformação)](#análise-e-limpeza-etapa-de-transformação)
4. [Detalhes técnicos](#detalhes-técnicos)
5. [Decisões de arquitetura](#decisões-de-arquitetura)
6. [Sondagem de cobertura](#sondagem-de-cobertura)
7. [Classificação do tipo de compra](#classificação-do-tipo-de-compra)
8. [Tabelas de preços e regras de economia](#tabelas-de-preços-e-regras-de-economia)
9. [Limitações conhecidas](#limitações-conhecidas)
10. [Referências](#referências)

---

## Uso no Google Colab

1. Abra `colab/coleta_vlr.ipynb` no Colab: em *Arquivo → Abrir notebook → GitHub*, procure
   `isa-dib/vlr_scraper` e escolha o arquivo.
2. Rode as células em ordem. O notebook:
   - monta o Drive;
   - baixa o código do GitHub para o disco local do Colab (`/content/codigo_vlr`), instala as
     dependências e confere que o pacote `vlr` importa;
   - opcionalmente, roda os testes;
   - grava todas as saídas em `MyDrive/TCC/vlr_dados/`.

Para usar uma cópia do código que está no Drive (por exemplo, com alterações locais), mude
`FONTE_CODIGO` para `"drive"` na célula 2 e coloque a pasta do projeto (ou um `.zip`) em
`MyDrive/TCC/vlr_scraper`. Pastas aninhadas e zips baixados do GitHub (`vlr_scraper-main/`)
são reconhecidos. Para mudar a pasta de saída, edite `PASTA_DADOS` na célula 1.

### Pasta de dados no Drive

```
MyDrive/TCC/vlr_dados/
├── raw/          páginas baixadas, comprimidas (.html.gz) — cache que permite retomar a coleta
├── processed/    tabelas finais em Parquet: rounds, mapas, partidas, jogadores_rounds,
│                 eventos_round, cobertura_por_evento, eventos_concluidos, sondagem_replay
├── amostra/      as mesmas tabelas em CSV, geradas a partir de 2 partidas salvas no projeto
└── logs/         coleta_<data>_<hora>.log (um por execução) e falhas.parquet
```

### Funções disponíveis no notebook (`from vlr import colab`)

| Função | O que faz |
|---|---|
| `colab.preparar(pasta, min_delay=3, max_delay=6)` | monta o Drive, cria as subpastas e define o intervalo entre requisições |
| `colab.amostra()` | gera os CSVs de amostra em `amostra/`, sem acessar o site |
| `colab.listar_eventos(paginas=None)` | eventos concluídos do tier 60 (todas as páginas) → `eventos_concluidos.parquet` |
| `colab.sondar(eventos=None)` | sondagem de 2D replay → `sondagem_replay.parquet` |
| `colab.coletar(eventos, max_partidas=None)` | baixa os eventos para `raw/` e gera os Parquet ao final |
| `colab.processar()` | regera os Parquet a partir de `raw/`, sem baixar nada |
| `colab.ler("rounds")` | lê uma tabela de `processed/` como DataFrame |
| `colab.falhas()` | lista o que falhou nas coletas (`logs/falhas.parquet`) |
| `colab.uso_de_espaco()` | tamanho e número de arquivos de cada subpasta |
| `colab.remover_copias_duplicadas()` | libera espaço em caches de versões anteriores (ver decisão 13) |
| `colab.finalizar()` | sincroniza e desmonta o Drive antes de fechar |

### Se o Colab desconectar

Sessões do Colab caem por inatividade (cerca de 90 min sem interação no plano gratuito) ou
pelo limite de duração (até ~12 h). Nada se perde:

1. Rode de novo as células 1, 2 e 4 (Drive, código e `preparar`).
2. Rode de novo a célula de coleta, com a mesma lista de eventos.

O que já está em `raw/` não é baixado de novo. Arquivos temporários de uma gravação
interrompida são apagados automaticamente no início da coleta.

**As tabelas de `processed/` só são geradas no fim da célula de coleta.** Se você parar a
célula pelo botão de parar, ou se a coleta terminar com erro, elas são geradas mesmo assim com o
que já foi baixado. Se o Colab cair antes disso, as páginas ficam em `raw/`, mas `processed/`
fica vazio ou desatualizado. Nesse caso, rode `colab.processar()`, que gera as tabelas sem baixar
nada. O notebook de análise lê `processed/` e avisa quando isso acontecer.

### Antes de rodar tudo: amostra e teste curto

1. **`colab.amostra()`** (ou `pytest`) processa duas partidas reais salvas em `tests/fixtures/`
   e grava, em CSV, exatamente as mesmas tabelas da coleta real:
   - **NRG vs T1** (Champions 2026, 2:0): Lotus (24 rounds) e Summit (20 rounds) com replay;
     Abyss aparece como `nao_jogado`.
   - **BLG vs XLG** (China Evolution Series 2026): Summit jogado `sem_replay`; Haven
     `nao_baixado` (fora da amostra de propósito); Sunset `nao_jogado`.

   Resultado: 44 linhas em `rounds.csv` (64 colunas), 440 em `jogadores_rounds.csv`,
   339 em `eventos_round.csv` e 6 em `mapas.csv`.
2. **`colab.coletar([2766], max_partidas=2)`** faz uma coleta real e curta e já grava os Parquet
   em `processed/`, para conferir o resultado com dados recém-baixados.

## Uso local (linha de comando)

```bash
cd vlr_scraper
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q tests                              # 28 testes com páginas reais
```

Os testes também regeram os CSVs de amostra do repositório em `data/amostra/`. Eles nunca
gravam na pasta de dados real. Requer Python 3.10 ou superior (testado com 3.13).

```bash
D=/caminho/da/pasta/de/dados        # sem --dados: usa $VLR_DADOS ou data/ do projeto

python -m vlr.crawl --dados $D amostra                                  # CSVs de exemplo, sem rede
python -m vlr.crawl --dados $D listar-eventos                           # todas as páginas (--paginas N limita)
python -m vlr.crawl --dados $D sondar                                   # todos os eventos (--eventos ... escolhe)
python -m vlr.crawl --dados $D coletar --eventos 2766 --max-partidas 2  # teste curto
python -m vlr.crawl --dados $D coletar --eventos 2766 2977 2976 2776    # coleta completa
python -m vlr.crawl --dados $D processar --precos config/outra_tabela.csv
```

Opções globais: `--dados`, `--min-delay` e `--max-delay` (intervalo entre requisições, padrão
3 a 6 s) e `-v` (log detalhado). `coletar --sem-processar` só baixa, sem gerar as tabelas.

### VS Code com dados já coletados

Nenhuma mudança no código é necessária: o mesmo código roda no Colab e no computador. Só muda a
pasta de dados. Para reaproveitar o que já está em `raw/` sem coletar de novo:

**1. Deixe a pasta `vlr_dados` acessível no computador** (escolha uma opção):
- **Google Drive para computador** (recomendado): a pasta aparece, por exemplo, como
  `G:\Meu Drive\TCC\vlr_dados` no Windows ou `~/Library/CloudStorage/GoogleDrive-…/Meu Drive/TCC/vlr_dados`
  no macOS. Antes de processar, clique com o botão direito em `vlr_dados` → *Disponibilizar
  off-line*. Ler milhares de arquivos sob demanda do Drive é bem mais lento.
- **Download:** em drive.google.com, clique com o botão direito em `vlr_dados` → *Fazer download*.
  O Drive divide pastas grandes em vários `.zip`; extraia todos na mesma pasta.

O que importa é a estrutura `vlr_dados/raw/event_*/match_*/*.html.gz`.

**2. Prepare o projeto no VS Code:**

```bash
git clone https://github.com/isa-dib/vlr_scraper
cd vlr_scraper
python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell);  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt   # inclui as bibliotecas do notebook de análise
python -m pytest -q tests
```

No VS Code, abra a pasta `vlr_scraper` e escolha o interpretador `.venv` (*Ctrl+Shift+P →
Python: Select Interpreter*). As extensões *Python* e *Jupyter* precisam estar instaladas.

**3. Gere as tabelas a partir de `raw/`, sem acessar o site:**

```bash
python -m vlr.crawl --dados "G:\Meu Drive\TCC\vlr_dados" processar
```

Use o caminho da sua pasta, entre aspas. O comando lê vários eventos em paralelo (um por núcleo do
processador, até 8; `--processos N` escolhe outro número e `--processos 1` desliga), mostra o
progresso evento a evento, com o tempo médio por partida, e grava os Parquet em
`vlr_dados/processed/`. Se a pasta for a do Google Drive para computador, eles também
sincronizam com o Drive. Com o mesmo `--dados`, os comandos `coletar` e `sondar` também funcionam
localmente, se um dia você quiser completar a coleta pelo computador.

**4. Rode a análise:** abra `analise/analise_descritiva.ipynb`, escolha o kernel `.venv` e, na
célula de configuração, deixe `FONTE = "drive"` e aponte `PASTA_DADOS` para a sua pasta, por
exemplo `PASTA_DADOS = r"G:\Meu Drive\TCC\vlr_dados"`. As saídas vão para
`vlr_dados/transformed/`.

### Sondagem de replay

O arquivo `docs/sondagem_replay.csv` traz a sondagem de **todos os eventos concluídos do tier 60**
(todas as páginas da lista), feita em 24 e 25/10/2026. Para cada evento, foram testados o 1º mapa da
primeira e o 1º mapa da última partida. A coluna `replay` resume o resultado: `sim` (as duas com
replay), `parcial` (só uma) ou `nao`. O resumo está em
[Sondagem de cobertura](#sondagem-de-cobertura).

## Arquivos gerados

As tabelas da coleta ficam em `<dados>/processed/` como **Parquet** (`rounds.parquet`,
`mapas.parquet`...); as de amostra, em `<dados>/amostra/` como **CSV**, com os mesmos nomes e
colunas. Para ler no notebook: `colab.ler("rounds")` ou `pd.read_parquet(".../rounds.parquet")`.

### `rounds` — uma linha por (mapa, round)

As colunas `team_a_*` se referem ao time da esquerda no vlr.gg (`team_a`), e as `team_b_*` ao da
direita. Os nomes `team_a`/`team_b` evitam confusão com a equipe T1, que existe de verdade. Nas
colunas de código (`winner`, `atk_team`, `winner_eco` e `team`/`actor_team`/`victim_team` em
`jogadores_rounds` e `eventos_round`), **1 = `team_a`** e **2 = `team_b`**. Na tabela abaixo,
`team_X_` vale para os dois times.

| Coluna | Descrição |
|---|---|
| `round_number`, `half`, `is_pistol_round`, `is_overtime` | posição do round (pistol = 1 e 13; overtime > 24) |
| `atk_team`, `team_a_side`, `team_b_side` | quem atacou no round |
| `winner`, `outcome_team_a` | vencedor (1 = `team_a`, 2 = `team_b`) e Win/Loss do ponto de vista do `team_a` |
| `win_type` | `eliminacao`, `spike_detonada`, `spike_defusada`, `tempo` |
| `team_a_score_after`, `team_b_score_after` | placar **após** o round (não usar como entrada do mesmo round) |
| `planted`, `plant_site`, `defused` | eventos de spike |
| `team_X_kills` | kills feitas por jogadores do time (sem fogo amigo, sem spike) |
| `team_X_deaths`, `team_X_spike_deaths`, `team_X_teamkills` | mortes sofridas, mortes pela explosão da spike, fogo amigo |
| `team_X_credits_left` | soma dos créditos restantes **após a fase de compra** |
| `team_X_spent` | soma do gasto na fase de compra (inclui habilidades) |
| `team_X_loadout_value` | soma do preço de arma e armadura dos 5 jogadores (calculado com `config/precos_itens.csv`) |
| `team_X_loadout_vlr` | valor de loadout do time **calculado pelo vlr.gg** (aba Economy, arredondado a 100) |
| `team_X_type_buy` | `Pistol`, `Eco`, `Half-buy` ou `Fullbuy`, conforme a classificação do vlr.gg |
| `team_X_bank_vlr` | banco do time na aba Economy (arredondado a 100; usado para conferência) |
| `team_X_n_heavy`, `team_X_n_light`, `team_X_n_regen`, `team_X_n_no_armor` | composição de armadura |
| `team_X_n_itens_sem_preco` | itens que não estão na tabela de preços (deve ser 0) |
| `winner_check_ok` | o vencedor pelo placar bate com o vencedor pela faixa de rounds |
| `banco_confere` | o banco da aba Economy bate com a soma exata dos créditos dos jogadores (±50) |
| `winner_eco`, `economia_status` | vencedor segundo a aba Economy e resultado da conferência com o replay |
| `patch`, `tabela_precos` | patch da partida e arquivo de preços usado |

### `mapas` — um mapa por linha, com `status`

| status | significado | entra no denominador de cobertura? |
|---|---|---|
| `ok` | jogado, replay lido com sucesso | sim |
| `sem_replay` | jogado, mas o site mostra "No 2D replay for this map" | sim |
| `nao_jogado` | listado no veto, mas **não jogado** (ex.: 3º mapa de série 2:0) | **não** |
| `nao_baixado` | jogado, mas o HTML ainda não está no cache | rode `coletar` de novo |
| `nao_renderizado` | o HTML veio sem o mapa (carregamento lazy) | apague o arquivo e colete de novo |
| `inconsistente` | trouxe rounds, mas alguma checagem falhou (detalhe em `status_detail`) | sim, revisar |

A coluna `economia_status` diz se a aba Economy daquele mapa foi lida e bate com o replay:
`ok`, `ausente`, `nao_baixada`, `nao_usada` (mapa sem replay) ou `inconsistente: <motivo>`.

### Demais arquivos

| Arquivo | Conteúdo |
|---|---|
| `cobertura_por_evento` | contagem por status e `pct_jogados_com_replay = ok / mapas_jogados` (mapas não jogados ficam de fora) |
| `jogadores_rounds` | economia por jogador e round, com preço de arma, armadura e loadout (auditoria) |
| `eventos_round` | kills, plants e defuses com tempo, autor, vítima, arma e tipo de morte |
| `partidas` | metadados da partida: data UTC, patch, times, veto, placar da série |
| `eventos_concluidos` | saída do comando `listar-eventos` (com a página de origem) |
| `sondagem_replay` | saída do comando `sondar` |
| `logs/falhas.parquet` | eventos e partidas que falharam, com data, etapa e erro |
| `logs/coleta_*.log` | log completo de cada execução |


### Análise e limpeza (etapa de transformação)

O notebook `analise/analise_descritiva.ipynb` é independente do código de coleta: ele só lê
`rounds`, `mapas` e `partidas` de `<dados>/processed/`. Com `FONTE = "amostra"`, lê os CSVs de
amostra direto do GitHub, para testar sem a coleta. Ele:

1. mostra o inventário da base coletada;
2. tira as partidas **fora do escopo** do estudo: showmatches, open qualifiers e séries Bo1
   (cada exclusão pode ser desligada na configuração; as qualificatórias fechadas continuam);
3. aplica regras explícitas de validade (nível mapa e nível round) e mostra o **funil de limpeza**:
   antes da limpeza, fora do escopo, inválidos por regra e válidos, em mapas e em rounds;
4. faz a análise descritiva e cria as variáveis processadas (`win_count`, `loss_count`,
   `score_diff`, `streak`, `loss_streak`, resultado do round anterior, tendência de kills,
   diferenças de loadout e créditos), todas calculadas só com o que se sabe antes do round;
5. faz a análise estatística ligada às hipóteses H1 a H3 (lado, tipo de compra, diferença de
   loadout, momentum controlado pela economia, conversão pós-pistol, tamanho do histórico e
   correlações), com IC 95% por bootstrap de mapas.

Saídas em `<dados>/transformed/` (Parquet): `rounds_validos`, `rounds_time` (formato por time,
com as variáveis processadas), `mapas_limpeza`, `partidas_escopo`, `funil_mapas`, `funil_rounds`, `regras_round`,
`resumo_limpeza` e as figuras em `figuras/`.

---

## Detalhes técnicos

### Principais bibliotecas

| Biblioteca | Versão testada | Uso |
|---|---|---|
| [requests](https://requests.readthedocs.io/) | 2.34 | requisições HTTP com `Session` (reaproveita conexão e cabeçalhos) |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) | 4.15 | navegação no HTML por seletores CSS (`select`, `select_one`) |
| [lxml](https://lxml.de/) | 6.1 | parser HTML usado pelo BeautifulSoup (mais rápido e tolerante; cai para `html.parser` se não estiver instalado) |
| [pandas](https://pandas.pydata.org/) | 3.0 | agregação por time, junção de tabelas e exportação |
| [pyarrow](https://arrow.apache.org/docs/python/) | 26.0 | gravação e leitura de Parquet (compressão zstd) |
| [pytest](https://docs.pytest.org/) | 9.1 | testes automatizados com páginas reais salvas |

Bibliotecas da própria linguagem: `json` (dados embutidos no HTML), `re` (números e títulos),
`gzip` (cache comprimido), `tempfile` e `os.replace` (gravação atômica), `dataclasses`
(registros tipados), `argparse` (linha de comando), `logging`, `pathlib` e `csv`. No Colab,
`google.colab.drive` monta o Google Drive.

### Estrutura do código

```
vlr_scraper/
├── vlr/
│   ├── parse.py       HTML -> registros (funções puras, sem rede)
│   ├── features.py    registros -> tabela por time e round (pandas)
│   ├── storage.py     pasta de dados, cache .html.gz, gravação atômica, Parquet/CSV
│   ├── crawl.py       HTTP, coleta, processamento e linha de comando
│   └── colab.py       funções de alto nível para o notebook
├── colab/
│   └── coleta_vlr.ipynb      notebook do Google Colab
├── config/
│   ├── precos_itens.csv      preço de armas e armaduras
│   └── regras_economia.csv   renda por vitória, derrota, kill, plant...
├── tests/
│   ├── fixtures/      páginas reais do vlr.gg salvas em out/2026
│   │   ├── events_tier60.html                   lista de eventos (página 1)
│   │   ├── event_2766_matches.html              partidas do Champions 2026
│   │   ├── match_754732_game_283271_logs.html   NRG vs T1, Lotus (2D Replay)
│   │   ├── match_754732_game_283272_logs.html   NRG vs T1, Summit (2D Replay)
│   │   ├── match_754732_economy.html            NRG vs T1, aba Economy
│   │   └── match_748619_noreplay_logs.html      BLG vs XLG, mapa sem replay
│   ├── conftest.py    garante que os testes não gravam na pasta de dados real
│   └── test_parse.py
├── docs/
│   └── sondagem_replay.csv   sondagem de todos os eventos concluídos do tier 60
└── data/amostra/      CSVs de amostra do repositório (regenerados pelos testes)
```

### Fluxo de dados

```
/events/?tier=60&page=1..N   -> eventos concluídos         (parse_events_list; N = parse_events_last_page)
/event/matches/<id>/...      -> partidas concluídas        (parse_event_matches)
/<partida>/?tab=logs         -> cabeçalho + lista de mapas (parse_match_header, parse_games_nav)
/<partida>/?game=<id>&tab=logs -> rounds do mapa           (parse_game_logs)
/<partida>/?tab=economy      -> loadout e tipo de compra de todos os mapas (parse_economy)
                                  └─ junção por (game_id, round) + features.team_round_table -> rounds.parquet
```

### Onde cada dado está na página

| Dado | Elemento HTML | Observação |
|---|---|---|
| Mapas da série | `.vm-stats-gamesnav-item[data-game-id]` | `data-disabled="1"` = não jogado |
| Conteúdo de um mapa | `.vm-stats-game[data-game-id]` | só o mapa pedido na URL vem renderizado; os outros vêm "Loading…" |
| Mapa sem replay | `.lg-empty` com "No 2D replay for this map" | |
| Resultado e tipo de vitória | `.js-lg-round[data-round]` | classe `mod-won-atk`/`mod-won-def` e ícone `elim`/`boom`/`defuse`/`time` |
| Placar após o round e lado | `.lg-band-team.mod-t1` / `.mod-t2` | classes `mod-atk`/`mod-def` |
| Economia do jogador | `.lg-mir-row` | `.lg-mir-left` (restante), `.lg-kit-spent` (gasto), `.lg-kit-shield` e `.lg-kit-gun` (`title`) |
| Atacante, jogadores e eventos | `.lg-map[data-map]` | JSON: `atk`, `players` (id → [time, agente, nick]) e `events` (kill/plant/defuse) |
| Banco do time (Economy) | `.ecr-bank.mod-t1` / `.mod-t2` | `title="NRG 0.2k"` |
| Loadout do time (Economy) | `.ecc-c.mod-t1` / `.mod-t2` | `title="21.6k"`; a faixa tem sempre 24 colunas, e as de rounds não jogados vêm vazias |
| Tipo de compra (Economy) | `.ecc-sq` | `mod-bp` Pistol · `mod-b1` Eco · `mod-b2` Half buy · `mod-b3` Full buy; `mod-win` marca o vencedor |

---

## Decisões de arquitetura

**1. Coleta e processamento separados (camada bruta + camada processada).**
`coletar` baixa e salva o HTML original e, ao final, chama o mesmo `processar`, que só lê o
disco. Assim, mudar uma regra
(por exemplo, a tabela de preços ou o critério de kill) não exige refazer requisições, e o
dado bruto fica disponível para auditoria e reprodutibilidade do TCC.

**2. Parsers puros, sem rede.**
Todas as funções de `parse.py` recebem uma string HTML e devolvem dados. Isso permite testar com
páginas reais salvas (`tests/fixtures`) e reprocessar milhares de arquivos em segundos. O
comando `amostra` aproveita isso: monta um cache temporário com as páginas salvas e passa pelo
mesmo `process()` da coleta real, então a amostra tem exatamente o formato final.

**3. Dados estruturados embutidos em vez de texto visível, sempre que possível.**
O atacante do round, o time de cada jogador e a lista de kills vêm do JSON no atributo
`data-map`, que é a fonte usada pelo próprio replay 2D. O texto visível (feed de kills, placar)
é usado para conferência e para o que não está no JSON, como a arma do abate.

**4. Checagens cruzadas e status explícito, em vez de falhar em silêncio.**
Cada mapa recebe um `status`. São três checagens: o vencedor pelo placar tem que bater com o
vencedor pela faixa de rounds; o número de kills no feed tem que bater com o JSON; e todo round
precisa ter 10 jogadores. Se alguma falhar, o mapa vira `inconsistente`, com o motivo em
`status_detail`.

**5. Mapa não jogado ≠ mapa sem replay.**
Mapas desabilitados na navegação (série encerrada antes) não são baixados e não entram no
cálculo de cobertura. A mensagem da aba "All Maps" é ignorada, porque não se refere a um mapa.

**6. Identidade do time pelo JSON, não pela posição na tela.**
O time de cada jogador vem de `players` no JSON; a posição esquerda/direita é só o plano B.
Isso evita erro quando os lados trocam no round 13.

**7. Formato largo (`team_a_*` / `team_b_*`) na saída.**
Uma linha por round com os dois times lado a lado facilita calcular diferenças (`loadout_diff`,
`credits_diff`) e escolher a perspectiva do modelo na etapa de transformação.

**8. Preços e regras fora do código.**
Os valores ficam em CSV (`config/`), com a fonte de cada linha. Para outro patch, basta outra
tabela; o nome do arquivo usado é gravado em `tabela_precos`.

**9. Variáveis derivadas ficam para a transformação.**
`win_count`, `loss_count`, `score_diff`, sequência de derrotas, conversão pós-pistol, rodada
bônus e defasagens não são calculadas aqui. Assim a coleta não embute decisões de modelagem nem
vazamento de dados (por exemplo, o placar *após* o round). A exceção é o tipo de compra, que o
próprio site publica (decisão 11).

**10. Coleta educada e retomável.**
Há um intervalo aleatório entre requisições, novas tentativas com espera crescente em HTTP
429/5xx, um User-Agent identificado e cache por arquivo. Uma partida ou um evento que falha é
registrado em `logs/falhas.parquet` e no log da execução, e a coleta segue para o próximo; ao
rodar de novo, o que faltou é baixado. O `robots.txt` do vlr.gg (out/2026) bloqueia apenas
`/search/auto` e `/rr/`, que não são usados. Custo: 1 requisição por evento; por partida,
1 da aba 2D Replay e 1 da aba Economy; e mais 1 por mapa jogado além do primeiro (a página da
partida já traz o 1º mapa, e a aba Economy traz todos os mapas de uma vez).

**11. Tipo de compra e loadout vindos do vlr.gg, não de uma tabela própria.**
A aba Economy publica, para cada round e time, o valor de loadout e a classe de compra. Usar
essa classificação mantém a variável igual à da fonte dos dados e evita aplicar os limiares do
site a um valor calculado de outro jeito: o `team_X_loadout_value` (arma + armadura) fica, em média,
cerca de 3.200 créditos abaixo do `team_X_loadout_vlr` na partida de exemplo, e reclassificar por ele
mudaria a classe em 7 de 48 rounds-time. O cálculo próprio continua na tabela como alternativa e
para auditoria. A junção com o replay é conferida pelo vencedor de cada round e pelo banco do
time (`economia_status` e `banco_confere`).

**12. Uma única pasta de dados, configurável (Drive no Colab, disco local fora dele).**
Todo caminho de saída vem de `vlr/storage.py`, a partir de uma pasta-base definida por
`colab.preparar(...)`, `--dados` ou pela variável `VLR_DADOS`. O código não conhece o Drive: no
Colab, a pasta-base simplesmente fica dentro de `/content/drive/MyDrive`. Assim o mesmo código
roda nos dois ambientes, e os testes usam uma pasta temporária (`tests/conftest.py`). O código é
copiado para o disco local do Colab, e só os dados vão para o Drive, porque importar módulos e
rodar testes direto do Drive é lento.

**13. Cache bruto comprimido (.html.gz).**
Cada página do vlr.gg tem de 300 a 850 KB, e uma coleta grande passa de dezenas de milhares de
páginas. Em gzip, elas ficam cerca de 8 a 10 vezes menores (as páginas de amostra caem de ~1,1 MB
para ~110 KB), o que cabe na cota do Drive e reduz o tráfego pelo Drive montado. A leitura aceita
também `.html` sem compressão (usada pela amostra).

Além disso, o mapa que já vem renderizado na página da partida (normalmente o 1º) não é gravado
de novo como `game_<id>.html`: o processamento o lê direto de `logs.html`. Isso corta cerca de
um quarto do volume de `raw/`. Caches criados por versões anteriores, que tinham essa cópia,
continuam funcionando, e `colab.remover_copias_duplicadas()` apaga as cópias para liberar espaço.

**14. Gravação atômica.**
Todo arquivo (HTML, Parquet ou CSV) é escrito primeiro num temporário `.tmp_*` na mesma pasta e só
depois renomeado para o nome final (`os.replace`). Como a retomada considera "existe = já baixado",
um arquivo pela metade com o nome final faria a página nunca ser refeita. Com a renomeação, uma
queda do Colab no meio da gravação deixa no máximo um `.tmp_*`, que é apagado no início da
próxima coleta.

**15. Tabelas em Parquet; amostras em CSV.**
O Parquet (compressão zstd) é colunar e guarda os tipos (inteiros, booleanos, texto), então a
leitura é mais rápida e não exige reconverter colunas. Em tabelas grandes, fica várias vezes menor
que o CSV equivalente (em tabelas minúsculas, como a amostra, o cabeçalho do formato pesa mais e
o CSV acaba menor). As amostras ficam em CSV porque servem para inspeção a olho, no Sheets ou no
Excel.

**16. Processamento em paralelo e leitura única da página da partida.**
Quase todo o tempo do `processar` é leitura de HTML (as páginas têm de 500 a 850 KB). A página da
partida é lida uma única vez e reaproveitada para o cabeçalho, a lista de mapas e o mapa ativo
(antes, ela era lida três vezes). Os eventos são processados em paralelo, um por núcleo, e
juntados sempre na mesma ordem, então o resultado não depende do número de processos (há um teste
que compara 1 e 2 processos). Os processos usam o modo `spawn`, porque `fork` pode travar dentro do
Jupyter/Colab. Medição com 36 partidas numa máquina de 2 núcleos: 1,18 s/partida antes; 0,81 s
com a leitura única; 0,42 s com 2 processos. Com mais núcleos, o ganho cresce quase na mesma
proporção.

---

## Sondagem de cobertura

Sondagem de todos os **265 eventos concluídos** do tier 60, nas 6 páginas da lista, feita em
24 e 25/10/2026 (`docs/sondagem_replay.csv`). As páginas estão em ordem cronológica
decrescente.

| Página | Período aproximado | Eventos | Com replay (`sim`) | `parcial` | Sem replay | Partidas nos eventos `sim` |
|---|---|---|---|---|---|---|
| 1 | 2024 a 2026 | 50 | 36 | 2 | 12 | 1.315 |
| 2 | 2022 a 2024 | 50 | 45 | 2 | 3 | 2.330 |
| 3 | 2021 a 2022 | 50 | 46 | 4 | 0 | 2.674 |
| 4 | 2021 (Stages 2 e 3) | 50 | 46 | 4 | 0 | 2.704 |
| 5 | 2021 (Stages 1 e 2) | 50 | 6 | 10 | 34 | 428 |
| 6 | 2021 (Stage 1) | 15 | 0 | 0 | 15 | 0 |
| **Total** | | **265** | **179** | **22** | **64** | **9.451** |

Padrões observados:

- **O 2D replay existe desde o VCT 2021 Stage 2** (abril de 2021). Os eventos do Stage 1 de
  2021 (Challengers e Masters, de janeiro a março) não têm replay.
- **China:** a China Evolution Series nunca tem replay, nem várias etapas chinesas de 2024 e 2025
  (Kickoffs, Stage 1/2 de 2024 e Masters Shanghai). Já o VCT 2026 China Stage 1 e 2 têm.
- **`parcial`** é um evento em que só a primeira ou só a última partida tem replay. A maioria são
  Challengers de 2021, em que o replay começou no meio do evento. Nesses casos, a coleta
  completa resolve: os mapas sem replay saem como `sem_replay` e são descartados.
- `sem_mapas` aparece quando a partida testada não tem lista de mapas (por exemplo, W.O.).

Atenção ao escopo: os 179 eventos `sim` somam cerca de 9.450 partidas, muitas delas de
Challengers regionais de 2021 e 2022 e de patches bem diferentes do atual. Vale escolher os
eventos pelo recorte do TCC (por exemplo, apenas VCT internacional e ligas de franquia de 2023
em diante). Com o intervalo padrão de 3 a 6 s, cada partida leva de 15 a 25 s para ser coletada.

---

## Classificação do tipo de compra

A variável `team_X_type_buy` segue a legenda da aba Economy do vlr.gg [3], aplicada ao valor de
loadout do time calculado pelo site (`team_X_loadout_vlr`):

| Tipo de compra | Loadout do time | Regra |
|---|---|---|
| Pistol | — | rounds 1 e 13 (início de cada metade, 800 créditos por jogador) |
| Eco | < 10.000 | |
| Half-buy | 10.000 a 18.000 | |
| Fullbuy | ≥ 18.000 | |

O teste `test_economia_respeita_limiares_da_legenda` confere que as classes publicadas respeitam
esses limiares (com tolerância de 100 pelo arredondamento do valor exibido).

A tabela segue a mesma ideia da Tabela 1 de Xenopoulos, Coelho e Silva [4] para o CS:GO, que
define o tipo de compra pelo equipamento e pelo gasto do time. Para comparação, há outras
classificações publicadas para o Valorant:

| Fonte | Eco | Intermediária | Full buy |
|---|---|---|---|
| vlr.gg, aba Economy atual [3] (usada aqui) | < 10k | Half buy 10–18k | ≥ 18k |
| vlr.gg, classificação anterior, via wabeg/valorant-round-predictor [5] | 0–5k (+ semi-eco 5–10k) | semi-buy 10–20k | ≥ 20k |
| Dataset VCT 2024 Pacific/EMEA, IEEE DataPort [6] | ≤ 10k | 10k–19,5k | ≥ 19,5k |

---

## Tabelas de preços e regras de economia

`config/precos_itens.csv` — preço de cada arma e armadura, conforme a Valorant Wiki [1],
conferido com o guia de economia da MIBR [2] para Vandal, Phantom, Spectre, Bulldog e armaduras.

| Categoria | Itens (créditos) |
|---|---|
| Pistolas | Classic 0 · Shorty 300 · Frenzy 450 · Ghost 500 · Bandit 600 · Sheriff 800 |
| SMGs | Stinger 1.100 · Spectre 1.600 |
| Escopetas | Bucky 850 · Judge 1.850 |
| Rifles | Bulldog 2.050 · Guardian 2.250 · Phantom 2.900 · Vandal 2.900 |
| Snipers | Marshal 950 · Outlaw 2.400 · Operator 4.700 |
| Metralhadoras | Ares 1.600 · Odin 3.200 |
| Armaduras | Light 400 · Regen Shield 650 · Heavy 1.000 |

`config/regras_economia.csv` — renda de créditos [1][2], para a etapa de transformação
(ex.: estimar o bônus de derrota e a economia do próximo round):

| Regra | Créditos |
|---|---|
| Início de cada metade (pistol) | 800 |
| Início de cada round de overtime | 5.000 |
| Limite por jogador | 9.000 |
| Vitória no round | 3.000 |
| Derrota: 1ª / 2ª seguida / 3ª seguida em diante | 1.900 / 2.400 / 2.900 |
| Derrota sobrevivendo (atacante sem plant ou defensor após detonação) | 1.000 |
| Por kill | 200 |
| Plant da spike (cada atacante) | 300 |

**Validação automática:** nos rounds de pistol (1 e 13), `credits_left + spent = 800` para
todos os jogadores. O teste `test_pistol_creditos_iniciais` confirma isso na partida de exemplo.

---

## Limitações conhecidas

- **Habilidades não aparecem na aba 2D Replay**, então `team_X_loadout_value` soma apenas arma e
  armadura. `spent` inclui habilidades, mas não inclui armas mantidas do round anterior.
- **O `team_X_loadout_vlr` é arredondado a 100 créditos** (o site exibe "21.6k") e o vlr.gg não
  documenta exatamente o que entra no cálculo. Na partida de exemplo ele fica acima de arma +
  armadura, o que sugere que inclui habilidades. A classe de compra (`team_X_type_buy`) vem pronta
  do site, então o arredondamento não a afeta.
- **Partidas sem aba Economy** (mensagem "Stats from this map are not available yet", comum nos
  eventos sem replay) ficam com `economia_status = ausente`, e as colunas `_vlr` e `type_buy`
  ficam vazias.
- **Preços variam por patch.** A tabela reflete a versão atual [1]. A Bandit e o Regen Shield
  foram adicionados no patch 9.10, e preços como o da Shorty e o da Bucky já mudaram ao longo
  do tempo. Para partidas antigas, crie uma tabela própria e rode
  `processar --precos config/precos_<patch>.csv`.
- **Mudanças no HTML do vlr.gg** quebram primeiro os testes em `tests/`. Os seletores estão
  documentados no topo de `vlr/parse.py` e na tabela acima.
- A sondagem testa só o 1º mapa da primeira e da última partida de cada evento. A cobertura real
  sai de `cobertura_por_evento.parquet` depois da coleta.
- **Sessões do Colab têm limite** (desconexão por inatividade e duração máxima). A coleta
  retoma de onde parou, mas não continua sozinha: é preciso reabrir o notebook e rodar as células
  de novo. Para coletas de muitas horas, divida os eventos em lotes.
- **O Drive montado é mais lento que um disco local.** O `processar` lê todos os arquivos de
  `raw/`; com dezenas de milhares de páginas, isso pode levar de vários minutos a algumas dezenas
  de minutos. Mesmo assim, ele só precisa rodar uma vez por coleta.
- **Gravações no Drive podem levar alguns segundos para sincronizar.** Rode `colab.finalizar()`
  antes de fechar o notebook.

---

## Referências

[1] VALORANT WIKI. *Credits*. Fandom. Disponível em: <https://valorant.fandom.com/wiki/Credits>.
Acesso em: 24 out. 2026. Fonte dos preços de armas e armaduras e das regras de renda de créditos.

[2] MIBR. *Economia no Valorant: eco, full buy e rodada bônus*. 7 out. 2026. Disponível em:
<https://mibr.gg/valorant/economia-eco-full-buy-rodada-bonus/>. Acesso em: 24 out. 2026.
Fonte complementar das regras de economia e dos preços das compras mais comuns.

[3] VLR.GG. Páginas de eventos e partidas (abas 2D Replay e Economy). Disponível em:
<https://www.vlr.gg/>. Exemplo da aba Economy:
<https://www.vlr.gg/754732/nrg-vs-t1-valorant-champions-2026-ubqf/?game=283271&tab=economy>.
Acesso em: 24 out. 2026. Fonte dos dados coletados e da classificação do tipo de compra.

[4] XENOPOULOS, P.; COELHO, B.; SILVA, C. *Optimal Team Economic Decisions in Counter-Strike*.
In: IJCAI 2021 Workshop on AI for Sports Analytics (AISA), 2021. arXiv:2109.12990. Tabela 1
(tipos de compra no CS:GO).

[5] WABEG. *valorant-round-predictor*. GitHub. Disponível em:
<https://github.com/wabeg/valorant-round-predictor>. Acesso em: 24 out. 2026. Classificação
anterior do vlr.gg e taxas de vitória por confronto de tipos de compra.

[6] *Valorant Champions Tour 2024: Pacific and EMEA Round Data*. IEEE DataPort. Disponível em:
<https://ieee-dataport.org/documents/valorant-champions-tour-2024-pacific-and-emea-round-data>.
Acesso em: 24 out. 2026. Limiares de eco, half buy e full buy por valor de loadout.
