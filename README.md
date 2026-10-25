# Coleta de dados do vlr.gg — TCC (predição de vitória por round em Valorant)

Scraper das abas **2D Replay** e **Economy** do vlr.gg. Gera uma tabela round a round
com economia, tipo de compra, kills, lado e resultado dos dois times, além de um
relatório de cobertura que separa **mapa não jogado** de **mapa sem replay**.

## Sumário

1. [Instalação](#instalação)
2. [Uso](#uso)
3. [Arquivos gerados](#arquivos-gerados)
4. [Detalhes técnicos](#detalhes-técnicos)
5. [Decisões de arquitetura](#decisões-de-arquitetura)
6. [Sondagem de cobertura](#sondagem-de-cobertura)
7. [Classificação do tipo de compra](#classificação-do-tipo-de-compra)
8. [Tabelas de preços e regras de economia](#tabelas-de-preços-e-regras-de-economia)
9. [Limitações conhecidas](#limitações-conhecidas)
10. [Referências](#referências)

---

## Instalação

```bash
cd vlr_scraper
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q tests                              # 22 testes com páginas reais
```

Os testes também geram os **CSVs de amostra** em `data/amostra/` (ver abaixo).

Requer Python 3.10 ou superior (testado com 3.13).

## Uso

```bash
# 0) ver o que é extraído, sem acessar o site -> data/amostra/*.csv
python -m vlr.crawl amostra

# 1) eventos concluídos do tier 60, TODAS as páginas -> data/processed/eventos_concluidos.csv
python -m vlr.crawl listar-eventos
#    só as N primeiras páginas:  --paginas N

# 2) sondagem de 2D replay (1ª e última partida de cada evento) -> data/processed/sondagem_replay.csv
python -m vlr.crawl sondar                              # todos os eventos concluídos
python -m vlr.crawl sondar --eventos 2766 2977          # só os escolhidos

# 3) coleta + processamento (retomável: o que já está em data/raw não é baixado de novo)
python -m vlr.crawl coletar --eventos 2766 --max-partidas 2    # teste curto, já gera os CSVs
python -m vlr.crawl coletar --eventos 2766 2977 2976 2776      # coleta completa
#    só baixar, sem gerar os CSVs:  --sem-processar

# 4) regerar os CSVs a partir do cache (ex.: após trocar a tabela de preços)
python -m vlr.crawl processar --precos config/outra_tabela.csv
```

Opções globais: `--min-delay` e `--max-delay` (intervalo entre requisições, padrão 3 a 6 s)
e `-v` (log detalhado).

### Antes de rodar tudo: amostra e teste curto

1. **`amostra`** (ou `pytest`) processa duas partidas reais salvas em `tests/fixtures/` e grava
   em `data/amostra/` exatamente os mesmos CSVs da coleta real:
   - **NRG vs T1** (Champions 2026, 2:0): Lotus (24 rounds) e Summit (20 rounds) com replay;
     Abyss aparece como `nao_jogado`.
   - **BLG vs XLG** (China Evolution Series 2026): Summit jogado `sem_replay`; Haven
     `nao_baixado` (fora da amostra de propósito); Sunset `nao_jogado`.

   Resultado: 44 linhas em `rounds.csv` (64 colunas), 440 em `jogadores_rounds.csv`,
   339 em `eventos_round.csv` e 6 em `mapas.csv`.
2. **`coletar --max-partidas 2`** faz uma coleta real e curta e, ao terminar, já grava os CSVs em
   `data/processed/`. Assim dá para conferir o resultado com dados recém-baixados antes da
   coleta completa.

### Sondagem de replay

O arquivo `docs/sondagem_replay.csv` traz a sondagem de **todos os eventos concluídos do tier 60**
(todas as páginas da lista), feita em 24 e 25/10/2026. Para cada evento, foram testados o 1º mapa da
primeira e o 1º mapa da última partida. A coluna `replay` resume o resultado: `sim` (as duas com
replay), `parcial` (só uma) ou `nao`. O resumo está em
[Sondagem de cobertura](#sondagem-de-cobertura).

## Arquivos gerados

### `rounds.csv` — uma linha por (mapa, round)

As colunas `t1_*` se referem ao time da esquerda no vlr.gg (`team1`), e as `t2_*` ao da direita.

| Coluna | Descrição |
|---|---|
| `round_number`, `half`, `is_pistol_round`, `is_overtime` | posição do round (pistol = 1 e 13; overtime > 24) |
| `atk_team`, `t1_side`, `t2_side` | quem atacou no round |
| `winner`, `outcome_t1` | vencedor (1/2) e Win/Loss do ponto de vista do `team1` |
| `win_type` | `eliminacao`, `spike_detonada`, `spike_defusada`, `tempo` |
| `team1_score_after`, `team2_score_after` | placar **após** o round (não usar como entrada do mesmo round) |
| `planted`, `plant_site`, `defused` | eventos de spike |
| `tN_kills` | kills feitas por jogadores do time N (sem fogo amigo, sem spike) |
| `tN_deaths`, `tN_spike_deaths`, `tN_teamkills` | mortes sofridas, mortes pela explosão da spike, fogo amigo |
| `tN_credits_left` | soma dos créditos restantes **após a fase de compra** |
| `tN_spent` | soma do gasto na fase de compra (inclui habilidades) |
| `tN_loadout_value` | soma do preço de arma e armadura dos 5 jogadores (calculado com `config/precos_itens.csv`) |
| `tN_loadout_vlr` | valor de loadout do time **calculado pelo vlr.gg** (aba Economy, arredondado a 100) |
| `tN_type_buy` | `Pistol`, `Eco`, `Half-buy` ou `Fullbuy`, conforme a classificação do vlr.gg |
| `tN_bank_vlr` | banco do time na aba Economy (arredondado a 100; usado para conferência) |
| `tN_n_heavy`, `tN_n_light`, `tN_n_regen`, `tN_n_no_armor` | composição de armadura |
| `tN_n_itens_sem_preco` | itens que não estão na tabela de preços (deve ser 0) |
| `winner_check_ok` | o vencedor pelo placar bate com o vencedor pela faixa de rounds |
| `banco_confere` | o banco da aba Economy bate com a soma exata dos créditos dos jogadores (±50) |
| `winner_eco`, `economia_status` | vencedor segundo a aba Economy e resultado da conferência com o replay |
| `patch`, `tabela_precos` | patch da partida e arquivo de preços usado |

### `mapas.csv` — um mapa por linha, com `status`

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
| `cobertura_por_evento.csv` | contagem por status e `pct_jogados_com_replay = ok / mapas_jogados` (mapas não jogados ficam de fora) |
| `jogadores_rounds.csv` | economia por jogador e round, com preço de arma, armadura e loadout (auditoria) |
| `eventos_round.csv` | kills, plants e defuses com tempo, autor, vítima, arma e tipo de morte |
| `partidas.csv` | metadados da partida: data UTC, patch, times, veto, placar da série |
| `eventos_concluidos.csv` | saída do comando `listar-eventos` (com a página de origem) |
| `sondagem_replay.csv` | saída do comando `sondar` |

Os CSVs da coleta real ficam em `data/processed/`; os da amostra, em `data/amostra/`.

---

## Detalhes técnicos

### Principais bibliotecas

| Biblioteca | Versão testada | Uso |
|---|---|---|
| [requests](https://requests.readthedocs.io/) | 2.34 | requisições HTTP com `Session` (reaproveita conexão e cabeçalhos) |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) | 4.15 | navegação no HTML por seletores CSS (`select`, `select_one`) |
| [lxml](https://lxml.de/) | 6.1 | parser HTML usado pelo BeautifulSoup (mais rápido e tolerante; cai para `html.parser` se não estiver instalado) |
| [pandas](https://pandas.pydata.org/) | 3.0 | agregação por time, junção de tabelas e exportação para CSV |
| [pytest](https://docs.pytest.org/) | 9.1 | testes automatizados com páginas reais salvas |

Bibliotecas da própria linguagem: `json` (dados embutidos no HTML), `re` (números e títulos),
`dataclasses` (registros tipados), `argparse` (linha de comando), `logging`, `pathlib` e `csv`.

### Estrutura do código

```
vlr_scraper/
├── vlr/
│   ├── parse.py       HTML -> registros (funções puras, sem rede)
│   ├── features.py    registros -> tabela por time e round (pandas)
│   └── crawl.py       HTTP, cache em disco e linha de comando
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
│   └── test_parse.py
├── docs/
│   └── sondagem_replay.csv   sondagem de todos os eventos concluídos do tier 60
└── data/              (criada na execução; fora do git)
    ├── raw/           HTML bruto
    ├── processed/     CSVs da coleta real
    └── amostra/       CSVs gerados a partir de tests/fixtures
```

### Fluxo de dados

```
/events/?tier=60&page=1..N   -> eventos concluídos         (parse_events_list; N = parse_events_last_page)
/event/matches/<id>/...      -> partidas concluídas        (parse_event_matches)
/<partida>/?tab=logs         -> cabeçalho + lista de mapas (parse_match_header, parse_games_nav)
/<partida>/?game=<id>&tab=logs -> rounds do mapa           (parse_game_logs)
/<partida>/?tab=economy      -> loadout e tipo de compra de todos os mapas (parse_economy)
                                  └─ junção por (game_id, round) + features.team_round_table -> rounds.csv
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

**7. Formato largo (`t1_*` / `t2_*`) na saída.**
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
429/5xx, um User-Agent identificado e cache por arquivo. Uma partida que falha é registrada no
log e não interrompe o evento. O `robots.txt` do vlr.gg (out/2026) bloqueia apenas
`/search/auto` e `/rr/`, que não são usados. Custo: 1 requisição por evento; por partida,
1 da aba 2D Replay e 1 da aba Economy; e mais 1 por mapa jogado além do primeiro (a página da
partida já traz o 1º mapa, e a aba Economy traz todos os mapas de uma vez).

**11. Tipo de compra e loadout vindos do vlr.gg, não de uma tabela própria.**
A aba Economy publica, para cada round e time, o valor de loadout e a classe de compra. Usar
essa classificação mantém a variável igual à da fonte dos dados e evita aplicar os limiares do
site a um valor calculado de outro jeito: o `tN_loadout_value` (arma + armadura) fica, em média,
cerca de 3.200 créditos abaixo do `tN_loadout_vlr` na partida de exemplo, e reclassificar por ele
mudaria a classe em 7 de 48 rounds-time. O cálculo próprio continua no CSV como alternativa e
para auditoria. A junção com o replay é conferida pelo vencedor de cada round e pelo banco do
time (`economia_status` e `banco_confere`).

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

A variável `tN_type_buy` segue a legenda da aba Economy do vlr.gg [3], aplicada ao valor de
loadout do time calculado pelo site (`tN_loadout_vlr`):

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

- **Habilidades não aparecem na aba 2D Replay**, então `tN_loadout_value` soma apenas arma e
  armadura. `spent` inclui habilidades, mas não inclui armas mantidas do round anterior.
- **O `tN_loadout_vlr` é arredondado a 100 créditos** (o site exibe "21.6k") e o vlr.gg não
  documenta exatamente o que entra no cálculo. Na partida de exemplo ele fica acima de arma +
  armadura, o que sugere que inclui habilidades. A classe de compra (`tN_type_buy`) vem pronta
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
  sai de `cobertura_por_evento.csv` depois da coleta.

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
