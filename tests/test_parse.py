"""Testes contra páginas reais do vlr.gg salvas em tests/fixtures (out/2026).

Valores esperados conferidos à mão na página
NRG vs T1 – Valorant Champions 2026, mapa 1 (Lotus), placar final 13-11.
"""
from pathlib import Path

import pandas as pd
import pytest

from vlr import parse as P
from vlr import crawl as C
from vlr.features import load_prices, team_round_table, player_loadout

FX = Path(__file__).parent / "fixtures"
LOTUS = (FX / "match_754732_game_283271_logs.html").read_text(encoding="utf-8")
NOREPLAY = (FX / "match_748619_noreplay_logs.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lotus():
    return P.parse_game_logs(LOTUS, 283271)


# ---------------------------------------------------------------- navegação de mapas

def test_mapa_nao_jogado_nao_e_confundido_com_sem_replay():
    """Série 2:0 -> Abyss aparece na navegação, mas desabilitado (não jogado)."""
    nav = P.parse_games_nav(LOTUS)
    assert [(g["map_name"], g["played"]) for g in nav] == [("Lotus", True), ("Summit", True), ("Abyss", False)]


def test_aba_all_maps_nao_e_tratada_como_mapa():
    assert all(g["game_id"] != "all" for g in P.parse_games_nav(LOTUS))


def test_mapa_jogado_sem_replay():
    nav = P.parse_games_nav(NOREPLAY)
    assert [(g["map_name"], g["played"]) for g in nav] == [("Summit", True), ("Haven", True), ("Sunset", False)]
    gl = P.parse_game_logs(NOREPLAY, 282202)
    assert gl.status == P.MAP_STATUS_NO_REPLAY
    assert gl.rounds == []


def test_mapa_lazy_nao_e_sem_replay():
    """Outros mapas na mesma página vêm como 'Loading…': precisam de nova requisição."""
    assert P.parse_game_logs(LOTUS, 283272).status == P.MAP_STATUS_NOT_RENDERED


# ---------------------------------------------------------------- cabeçalho

def test_cabecalho():
    h = P.parse_match_header(LOTUS)
    assert h["match_id"] == 754732
    assert (h["team1"], h["team2"]) == ("NRG", "T1")
    assert h["patch"] == "13.05"
    assert h["series_score"] == "2:0"
    assert h["event_name"] == "Valorant Champions 2026"


# ---------------------------------------------------------------- rounds

def test_rounds_e_placar_final(lotus):
    assert lotus.status == P.MAP_STATUS_OK, lotus.status_detail
    assert len(lotus.rounds) == 24
    last = lotus.rounds[-1]
    assert (last.team1_score_after, last.team2_score_after) == (13, 11)
    assert all(r.winner_check_ok for r in lotus.rounds)


def test_resultado_e_tipo_de_vitoria(lotus):
    r = {x.round_number: x for x in lotus.rounds}
    assert (r[1].winner, r[1].win_type) == (2, "eliminacao")
    assert (r[3].winner, r[3].win_type, r[3].defused) == (1, "spike_defusada", True)
    assert (r[4].winner, r[4].win_type) == (2, "spike_detonada")
    assert r[11].win_type == "tempo"


def test_lados_trocam_no_round_13(lotus):
    atk = {x.round_number: x.atk_team for x in lotus.rounds}
    assert {atk[i] for i in range(1, 13)} == {2}
    assert {atk[i] for i in range(13, 25)} == {1}


def test_economia_jogador(lotus):
    p = [x for x in lotus.players if x.round_number == 4 and x.player == "keiko"][0]
    # manteve Vandal + Heavy do round anterior: gastou só 500
    assert (p.credits_left, p.spent, p.armor, p.weapon, p.team) == (2700, 500, "Heavy armor", "Vandal", 1)


def test_pistol_creditos_iniciais(lotus):
    pist = [x for x in lotus.players if x.round_number in (1, 13)]
    assert len(pist) == 20
    assert {x.credits_left + x.spent for x in pist} == {800}


def test_kills_e_mortes_pela_spike(lotus):
    E = pd.DataFrame(P.as_dicts(lotus.events))
    r18 = E[(E.round_number == 18) & (E.type == "kill")]
    assert (r18.kill_kind == "jogador").sum() == 1          # skuba mata Meteor
    assert (r18.kill_kind == "spike_ou_ambiente").sum() == 4  # 4 de T1 morrem na explosão
    assert set(r18[r18.kill_kind == "spike_ou_ambiente"].victim_team) == {2}


# ---------------------------------------------------------------- tabela por time

def test_tabela_por_time(lotus):
    R = pd.DataFrame(P.as_dicts(lotus.rounds))
    Pl = pd.DataFrame(P.as_dicts(lotus.players))
    E = pd.DataFrame(P.as_dicts(lotus.events))
    T = team_round_table(R, Pl, E, load_prices()).set_index("round_number")
    # round 1: NRG com Bandit, Sheriff, Classic, Ghost, Ghost, sem armadura
    assert T.loc[1, "t1_loadout_value"] == 600 + 800 + 0 + 500 + 500
    assert (T.loc[1, "t1_kills"], T.loc[1, "t2_kills"]) == (3, 5)
    assert (T.loc[18, "t1_kills"], T.loc[18, "t2_spike_deaths"]) == (1, 4)
    assert T.loc[18, "outcome_t1"] == "Win"
    assert T["t1_credits_left"].max() <= 45000
    assert (T["t1_n_itens_sem_preco"] + T["t2_n_itens_sem_preco"]).sum() == 0


def test_tabela_de_precos_conforme_fontes():
    pr = load_prices()
    assert (pr["Shorty"], pr["Bucky"], pr["Bandit"], pr["Regen Shield"]) == (300, 850, 600, 650)
    assert (pr["Vandal"], pr["Phantom"], pr["Operator"], pr["Heavy armor"]) == (2900, 2900, 4700, 1000)


# ---------------------------------------------------------------- listas

def test_lista_eventos():
    ev = P.parse_events_list((FX / "events_tier60.html").read_text(encoding="utf-8"))
    done = [e for e in ev if e["status"] == "completed"]
    assert len(done) == 50
    assert any(e["event_id"] == 2766 for e in done)


def test_numero_de_paginas_de_eventos():
    """listar-eventos usa isto para ler TODAS as páginas por padrão."""
    assert P.parse_events_last_page((FX / "events_tier60.html").read_text(encoding="utf-8")) == 6


def test_status_da_sondagem():
    assert C._probe_status(LOTUS) == "ok"
    assert C._probe_status(NOREPLAY) == "sem_replay"


def test_lista_partidas():
    m = P.parse_event_matches((FX / "event_2766_matches.html").read_text(encoding="utf-8"))
    assert len(m) == 34 and all(x["status"] == "completed" for x in m)
    assert any(x["match_id"] == 754732 for x in m)


# ---------------------------------------------------------------- aba Economy

ECONOMY = (FX / "match_754732_economy.html").read_text(encoding="utf-8")


def test_economia_traz_os_dois_mapas_jogados():
    eco = P.parse_economy(ECONOMY)
    assert set(eco) == {283271, 283272}          # Abyss não jogado não aparece
    assert len(eco[283271]) == 24
    assert len(eco[283272]) == 20                # colunas vazias (rounds 21-24) são ignoradas


def test_economia_valores_e_tipo_de_compra():
    r = {x.round_number: x for x in P.parse_economy(ECONOMY)[283271]}
    assert (r[1].t1_type_buy, r[1].t2_type_buy) == ("Pistol", "Pistol")
    assert (r[2].t1_loadout_vlr, r[2].t1_type_buy) == (2300, "Eco")
    assert (r[2].t2_loadout_vlr, r[2].t2_type_buy) == (15100, "Half-buy")
    assert (r[3].t1_loadout_vlr, r[3].t1_type_buy) == (21600, "Fullbuy")
    assert (r[13].t1_type_buy, r[13].t2_type_buy) == ("Pistol", "Pistol")
    assert r[1].t1_bank_vlr == 200


def test_economia_respeita_limiares_da_legenda():
    """Eco < 10k, Half buy 10–18k, Full buy 18k+ (tolerância de 100 pelo arredondamento)."""
    for rounds in P.parse_economy(ECONOMY).values():
        for x in rounds:
            for v, b in ((x.t1_loadout_vlr, x.t1_type_buy), (x.t2_loadout_vlr, x.t2_type_buy)):
                if b == "Eco":
                    assert v <= 10000
                elif b == "Half-buy":
                    assert 9900 <= v <= 18000
                elif b == "Fullbuy":
                    assert v >= 17900


def test_economia_bate_com_replay(lotus):
    eco = P.parse_economy(ECONOMY)[283271]
    assert [e.winner_eco for e in eco] == [r.winner for r in lotus.rounds]
    assert C._economy_status(lotus.rounds, eco, True) == "ok"


# ---------------------------------------------------------------- processamento ponta a ponta
# Este teste também GERA os CSVs de amostra em data/amostra/, para inspecionar
# o que a coleta extrai antes de rodar tudo (equivale a `python -m vlr.crawl amostra`).

def test_amostra_gera_csvs():
    out = C.SAMPLE_OUT
    res = C.build_sample(out)

    for name in ("partidas", "mapas", "rounds", "jogadores_rounds", "eventos_round", "cobertura_por_evento"):
        assert (out / f"{name}.csv").exists(), name

    st = res["mapas"].set_index("game_id")["status"].to_dict()
    assert st[283271] == "ok"            # Lotus
    assert st[283272] == "ok"            # Summit
    assert st[283273] == "nao_jogado"    # Abyss: série acabou 2:0
    assert st[282202] == "sem_replay"    # China Evolution Series: jogado, sem replay
    assert st[282203] == "nao_baixado"   # de propósito, fora da amostra
    assert st[282204] == "nao_jogado"

    R = res["rounds"]
    assert len(R) == 24 + 20
    assert R["banco_confere"].all()
    assert R["winner_check_ok"].all()
    lotus = R[R.game_id == 283271].set_index("round_number")
    assert (lotus.loc[3, "t1_type_buy"], lotus.loc[3, "t1_loadout_vlr"]) == ("Fullbuy", 21600)
    summit = R[R.game_id == 283272]
    assert (summit.team1_score_after.iloc[-1], summit.team2_score_after.iloc[-1]) == (13, 7)
    assert set(res["mapas"].dropna(subset=["economia_status"]).query("status == 'ok'")["economia_status"]) == {"ok"}

    cov = res["cobertura_por_evento"].set_index("event_id")
    # o mapa não jogado NÃO entra no denominador
    assert cov.loc[2766, "mapas_jogados"] == 2
    assert cov.loc[2766, "pct_jogados_com_replay"] == 100.0
    assert cov.loc[2766, "nao_jogado"] == 1
    assert cov.loc[3105, "sem_replay"] == 1
