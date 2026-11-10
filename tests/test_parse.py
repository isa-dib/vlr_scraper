"""Testes contra páginas reais do vlr.gg salvas em tests/fixtures (out/2026).

Valores esperados conferidos à mão na página
NRG vs T1 – Valorant Champions 2026, mapa 1 (Lotus), placar final 13-11.
"""
from pathlib import Path

import pandas as pd
import pytest

from vlr import parse as P
from vlr import crawl as C
from vlr import storage as S
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
    assert (h["team_a"], h["team_b"]) == ("NRG", "T1")
    assert h["patch"] == "13.05"
    assert h["series_score"] == "2:0"
    assert h["event_name"] == "Valorant Champions 2026"


# ---------------------------------------------------------------- rounds

def test_rounds_e_placar_final(lotus):
    assert lotus.status == P.MAP_STATUS_OK, lotus.status_detail
    assert len(lotus.rounds) == 24
    last = lotus.rounds[-1]
    assert (last.team_a_score_after, last.team_b_score_after) == (13, 11)
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
    assert T.loc[1, "team_a_loadout_value"] == 600 + 800 + 0 + 500 + 500
    assert (T.loc[1, "team_a_kills"], T.loc[1, "team_b_kills"]) == (3, 5)
    assert (T.loc[18, "team_a_kills"], T.loc[18, "team_b_spike_deaths"]) == (1, 4)
    assert T.loc[18, "outcome_team_a"] == "Win"
    assert T["team_a_credits_left"].max() <= 45000
    assert (T["team_a_n_itens_sem_preco"] + T["team_b_n_itens_sem_preco"]).sum() == 0


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
    assert (r[1].team_a_type_buy, r[1].team_b_type_buy) == ("Pistol", "Pistol")
    assert (r[2].team_a_loadout_vlr, r[2].team_a_type_buy) == (2300, "Eco")
    assert (r[2].team_b_loadout_vlr, r[2].team_b_type_buy) == (15100, "Half-buy")
    assert (r[3].team_a_loadout_vlr, r[3].team_a_type_buy) == (21600, "Fullbuy")
    assert (r[13].team_a_type_buy, r[13].team_b_type_buy) == ("Pistol", "Pistol")
    assert r[1].team_a_bank_vlr == 200


def test_economia_respeita_limiares_da_legenda():
    """Eco < 10k, Half buy 10–18k, Full buy 18k+ (tolerância de 100 pelo arredondamento)."""
    for rounds in P.parse_economy(ECONOMY).values():
        for x in rounds:
            for v, b in ((x.team_a_loadout_vlr, x.team_a_type_buy), (x.team_b_loadout_vlr, x.team_b_type_buy)):
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
    out = S.PROJECT_ROOT / "data" / "amostra"     # amostra do repositório, sempre em CSV
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
    assert (lotus.loc[3, "team_a_type_buy"], lotus.loc[3, "team_a_loadout_vlr"]) == ("Fullbuy", 21600)
    summit = R[R.game_id == 283272]
    assert (summit.team_a_score_after.iloc[-1], summit.team_b_score_after.iloc[-1]) == (13, 7)
    assert set(res["mapas"].dropna(subset=["economia_status"]).query("status == 'ok'")["economia_status"]) == {"ok"}

    cov = res["cobertura_por_evento"].set_index("event_id")
    # o mapa não jogado NÃO entra no denominador
    assert cov.loc[2766, "mapas_jogados"] == 2
    assert cov.loc[2766, "pct_jogados_com_replay"] == 100.0
    assert cov.loc[2766, "nao_jogado"] == 1
    assert cov.loc[3105, "sem_replay"] == 1


# ---------------------------------------------------------------- armazenamento (Drive/Colab)

def test_raw_store_gzip_e_leitura_de_html_sem_compressao(tmp_path):
    st = S.RawStore(tmp_path)
    st.write("event_1/match_2/logs.html", LOTUS)
    gz = tmp_path / "event_1/match_2/logs.html.gz"
    assert gz.exists() and not (tmp_path / "event_1/match_2/logs.html").exists()
    assert gz.stat().st_size < len(LOTUS.encode()) / 5          # bem menor que o HTML
    assert st.read("event_1/match_2/logs.html") == LOTUS
    (tmp_path / "event_9").mkdir()
    (tmp_path / "event_9/antigo.html").write_text("ok", encoding="utf-8")
    assert st.exists("event_9/antigo.html") and st.read("event_9/antigo.html") == "ok"


def test_gravacao_atomica_nao_deixa_arquivo_parcial(tmp_path, monkeypatch):
    st = S.RawStore(tmp_path)

    def explode(*a, **k):
        raise KeyboardInterrupt  # simula o Colab sendo interrompido no meio da gravação
    monkeypatch.setattr(S.os, "replace", explode)
    with pytest.raises(KeyboardInterrupt):
        st.write("event_1/x.html", "conteudo")
    assert not st.exists("event_1/x.html")              # nada com o nome final
    assert not list(tmp_path.rglob(".tmp_*"))           # e o temporário foi limpo


def test_processar_grava_parquet(tmp_path):
    S.configurar(tmp_path / "dados")
    st = S.RawStore(S.paths().raw)
    for dest, src in C.SAMPLE_LAYOUT.items():
        st.write(dest, (FX / src).read_text(encoding="utf-8"))
    res = C.process()
    proc = S.paths().processed
    for name in ("partidas", "mapas", "rounds", "jogadores_rounds", "eventos_round", "cobertura_por_evento"):
        assert (proc / f"{name}.parquet").exists(), name
        assert not (proc / f"{name}.csv").exists()
    R = pd.read_parquet(proc / "rounds.parquet")
    assert len(R) == 44 and R.shape[1] == res["rounds"].shape[1]
    assert R["team_a_type_buy"].notna().all()


def test_coleta_segue_quando_um_evento_falha(tmp_path, monkeypatch):
    S.configurar(tmp_path / "dados")
    f = C.Fetcher(0, 0)

    def fake_event_matches(fetcher, eid):
        if eid == 1:
            raise RuntimeError("HTTP 500 simulado")
        return []
    monkeypatch.setattr(C, "_event_matches", fake_event_matches)
    resumo = C.collect(f, [1, 2])
    assert resumo["eventos_falha"] == 1 and resumo["eventos"] == 1
    falhas = pd.read_parquet(S.paths().logs / "falhas.parquet")
    assert list(falhas["event_id"]) == [1]


def test_coleta_nao_grava_copia_do_mapa_ativo(tmp_path, monkeypatch):
    """O 1º mapa vem em logs.html: só os demais mapas viram game_<id>.html."""
    S.configurar(tmp_path / "dados")
    f = C.Fetcher(0, 0)
    pages = {"game=283272": (FX / "match_754732_game_283272_logs.html").read_text(encoding="utf-8"),
             "tab=economy": ECONOMY, "tab=logs": LOTUS}   # ordem importa: "game=" antes de "tab=logs"
    pedidos = []

    def fake_get(path, key, refresh=False):
        pedidos.append(path)
        html = next(v for k, v in pages.items() if k in path)
        f.store.write(key, html)
        return html
    monkeypatch.setattr(f, "get", fake_get)
    C._fetch_match(f, 2766, {"match_id": 754732, "match_url": "/754732/nrg-vs-t1/"})
    files = sorted(p.name for p in (S.paths().raw / "event_2766" / "match_754732").iterdir())
    assert files == ["economy.html.gz", "game_283272.html.gz", "logs.html.gz"]
    assert not any("game=283271" in p for p in pedidos)   # Lotus não é baixado de novo
    assert not any("game=283273" in p for p in pedidos)   # Abyss não jogado
    res = C.process()
    st = res["mapas"].set_index("game_id")["status"].to_dict()
    assert st[283271] == "ok" and st[283272] == "ok"


def test_remover_copias_duplicadas_de_cache_antigo(tmp_path):
    S.configurar(tmp_path / "dados")
    st = S.RawStore(S.paths().raw)
    st.write("event_2766/match_754732/logs.html", LOTUS)
    st.write("event_2766/match_754732/game_283271.html", LOTUS)   # cópia gravada pela versão antiga
    st.write("event_2766/match_754732/economy.html", ECONOMY)
    assert C.remover_copias_duplicadas()["removidas"] == 1
    assert not st.exists("event_2766/match_754732/game_283271.html")
    assert C.process()["mapas"].set_index("game_id").loc[283271, "status"] == "ok"


def test_coletar_interrompida_ainda_gera_as_tabelas(tmp_path, monkeypatch):
    """Parar a célula de coleta não pode deixar processed/ vazio."""
    from vlr import colab
    colab.preparar(tmp_path / "dados", montar=False)
    st = S.RawStore(S.paths().raw)
    for dest, src in C.SAMPLE_LAYOUT.items():
        st.write(dest, (FX / src).read_text(encoding="utf-8"))

    def interrompe(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(C, "collect", interrompe)
    colab.coletar([2766])
    assert (S.paths().processed / "rounds.parquet").exists()
    assert len(pd.read_parquet(S.paths().processed / "rounds.parquet")) == 44


def test_processar_em_paralelo_da_o_mesmo_resultado(tmp_path):
    S.configurar(tmp_path / "dados")
    st = S.RawStore(S.paths().raw)
    for dest, src in C.SAMPLE_LAYOUT.items():
        st.write(dest, (FX / src).read_text(encoding="utf-8"))
    seq = C.process(out_dir=tmp_path / "seq", processos=1)
    par = C.process(out_dir=tmp_path / "par", processos=2)
    for nome in ("partidas", "mapas", "rounds", "jogadores_rounds", "eventos_round", "cobertura_por_evento"):
        pd.testing.assert_frame_equal(seq[nome], par[nome], check_dtype=False)


def test_processar_gera_lista_do_tier60_a_partir_do_cache(tmp_path):
    S.configurar(tmp_path / "dados")
    st = S.RawStore(S.paths().raw)
    for dest, src in C.SAMPLE_LAYOUT.items():
        st.write(dest, (FX / src).read_text(encoding="utf-8"))
    ev = C.process()["eventos_tier60"]
    assert {2766, 3105} <= set(ev["event_id"])
    assert (ev["paginas_total"] == 6).all() and not ev["lista_completa"].any()   # só a página 1 no cache
    assert (S.paths().processed / "eventos_tier60.parquet").exists()


def test_sem_paginas_de_eventos_nao_gera_a_lista(tmp_path):
    S.configurar(tmp_path / "dados")
    st = S.RawStore(S.paths().raw)
    st.write("event_2766/match_754732/logs.html", LOTUS)
    assert "eventos_tier60" not in C.process()
