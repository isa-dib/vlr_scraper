"""Coleta do vlr.gg (camada bruta) e processamento para CSV.

Uso (a partir da pasta vlr_scraper/):

    # 1) ver os eventos concluídos do tier 60 (VCT)
    python -m vlr.crawl listar-eventos --paginas 2

    # 2) checar rapidamente quais eventos têm 2D replay (1 partida por evento)
    python -m vlr.crawl sondar --eventos 2766 2977 2776

    # 3) baixar tudo dos eventos escolhidos (fica em data/raw, com cache)
    python -m vlr.crawl coletar --eventos 2766 2977 2776

    # 4) gerar os CSVs a partir do cache (pode rodar quantas vezes quiser)
    python -m vlr.crawl processar

A coleta é retomável: o que já está em data/raw não é baixado de novo.
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from . import parse as P
from .features import load_prices, team_round_table, player_loadout, DEFAULT_PRICES

BASE = "https://www.vlr.gg"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

log = logging.getLogger("vlr")


# ----------------------------------------------------------------------------
# HTTP educado: intervalo aleatório, retry com backoff, cache em disco
# ----------------------------------------------------------------------------

class Fetcher:
    def __init__(self, min_delay: float = 3.0, max_delay: float = 6.0, user_agent: str | None = None):
        self.min_delay, self.max_delay = min_delay, max_delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent or (
            "Mozilla/5.0 (compatible; TCC-academic-scraper/1.0; coleta para pesquisa academica)"
        )
        self._last = 0.0
        self.n_requests = 0

    def _wait(self):
        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.time() - self._last
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(self, path: str, cache: Path, refresh: bool = False) -> str:
        if cache.exists() and not refresh:
            return cache.read_text(encoding="utf-8")
        url = path if path.startswith("http") else BASE + path
        for attempt in range(5):
            self._wait()
            self._last = time.time()
            try:
                r = self.s.get(url, timeout=30)
                self.n_requests += 1
            except requests.RequestException as e:
                log.warning("erro de rede em %s (%s), tentativa %d", url, e, attempt + 1)
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code == 200:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(r.text, encoding="utf-8")
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                wait = 30 * (attempt + 1)
                log.warning("HTTP %s em %s; aguardando %ds", r.status_code, url, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {r.status_code} em {url}")
        raise RuntimeError(f"falhou após 5 tentativas: {url}")


# ----------------------------------------------------------------------------
# caminhos do cache
# ----------------------------------------------------------------------------

def events_page_cache(page: int) -> Path:
    return RAW / "eventos" / f"tier60_page_{page}.html"


def event_matches_cache(eid: int) -> Path:
    return RAW / f"event_{eid}" / "matches.html"


def match_dir(eid: int, mid: int) -> Path:
    return RAW / f"event_{eid}" / f"match_{mid}"


# ----------------------------------------------------------------------------
# comandos
# ----------------------------------------------------------------------------

def list_events(f: Fetcher, pages: int | None = None, refresh: bool = False) -> pd.DataFrame:
    """Eventos concluídos do tier 60.

    pages=None (padrão) lê TODAS as páginas: o número da última é lido da
    paginação da primeira página. Um inteiro limita a leitura às N primeiras.
    """
    first = f.get("/events/?tier=60&page=1", events_page_cache(1), refresh=refresh)
    last = P.parse_events_last_page(first)
    if pages is not None:
        last = min(last, pages)
    log.info("lendo %d página(s) de eventos", last)
    rows = [dict(r, page=1) for r in P.parse_events_list(first)]
    for page in range(2, last + 1):
        html = f.get(f"/events/?tier=60&page={page}", events_page_cache(page), refresh=refresh)
        rows += [dict(r, page=page) for r in P.parse_events_list(html)]
    df = pd.DataFrame(rows).drop_duplicates("event_id")
    return df[df["status"] == "completed"].reset_index(drop=True)


def _event_matches(f: Fetcher, eid: int) -> list[dict]:
    html = f.get(f"/event/matches/{eid}/x/?series_id=all", event_matches_cache(eid))
    return [m for m in P.parse_event_matches(html) if m["status"] == "completed"]


def _fetch_match(f: Fetcher, eid: int, m: dict) -> dict:
    """Baixa a aba 2D Replay de todos os mapas JOGADOS de uma partida."""
    mid = m["match_id"]
    d = match_dir(eid, mid)
    base_path = m["match_url"].rstrip("/")
    html = f.get(f"{base_path}/?tab=logs", d / "logs.html")
    nav = P.parse_games_nav(html)
    active = P.active_game_id(html)
    fetched = 0
    if any(g["played"] for g in nav):
        # aba Economy: uma requisição traz loadout e tipo de compra de todos os mapas
        if not (d / "economy.html").exists():
            f.get(f"{base_path}/?tab=economy", d / "economy.html")
            fetched += 1
    for g in nav:
        if not g["played"]:
            continue  # mapa não jogado: não existe replay a buscar
        cache = d / f"game_{g['game_id']}.html"
        if g["game_id"] == active and not cache.exists():
            cache.write_text(html, encoding="utf-8")  # a página base já traz o 1º mapa
            continue
        if not cache.exists():
            f.get(f"{base_path}/?game={g['game_id']}&tab=logs", cache)
            fetched += 1
    return {"match_id": mid, "maps": len(nav), "fetched": fetched}


def collect(f: Fetcher, event_ids: list[int], max_matches: int | None = None):
    for eid in event_ids:
        matches = _event_matches(f, eid)
        if max_matches:
            matches = matches[:max_matches]
        log.info("evento %s: %d partidas concluídas", eid, len(matches))
        for i, m in enumerate(matches, 1):
            try:
                info = _fetch_match(f, eid, m)
                log.info("  [%d/%d] partida %s ok (%d mapas listados)", i, len(matches), m["match_id"], info["maps"])
            except Exception as e:  # não derruba a coleta inteira
                log.error("  [%d/%d] partida %s falhou: %s", i, len(matches), m["match_id"], e)


def _probe_status(html: str) -> str:
    gid = P.active_game_id(html)
    if gid is None:
        return "sem_mapas"
    gl = P.parse_game_logs(html, gid)
    return "ok" if gl.rounds else gl.status


def probe(f: Fetcher, events: list[int] | pd.DataFrame) -> pd.DataFrame:
    """Diz se há 2D replay olhando o 1º mapa da PRIMEIRA e da ÚLTIMA partida de cada evento.

    Testar as duas pontas pega eventos em que o replay só passou a existir no meio
    (ex.: fase de grupos sem replay e playoffs com replay).
    """
    if isinstance(events, pd.DataFrame):
        info = events.set_index("event_id").to_dict("index")
        ids = list(info)
    else:
        info, ids = {}, list(events)
    rows = []
    for eid in ids:
        meta = info.get(eid, {})
        base = {"event_id": eid, "pagina": meta.get("page"), "evento": meta.get("event_name"),
                "datas": meta.get("dates")}
        try:
            matches = _event_matches(f, eid)
            if not matches:
                rows.append({**base, "partidas_concluidas": 0})
                continue
            res = []
            for m in (matches[0], matches[-1]):
                html = f.get(f"{m['match_url'].rstrip('/')}/?tab=logs", match_dir(eid, m["match_id"]) / "logs.html")
                if base["evento"] is None:
                    base["evento"] = P.parse_match_header(html)["event_name"]
                res.append(_probe_status(html))
            rows.append({**base, "partidas_concluidas": len(matches),
                         "primeira_partida_mapa1": res[0], "ultima_partida_mapa1": res[1]})
        except Exception as e:
            log.error("evento %s falhou na sondagem: %s", eid, e)
            rows.append({**base, "erro": str(e)})
    df = pd.DataFrame(rows)
    if len(df) and "primeira_partida_mapa1" in df:
        both = df["primeira_partida_mapa1"].eq("ok") & df["ultima_partida_mapa1"].eq("ok")
        one = df["primeira_partida_mapa1"].eq("ok") | df["ultima_partida_mapa1"].eq("ok")
        df["replay"] = "nao"
        df.loc[one, "replay"] = "parcial"
        df.loc[both, "replay"] = "sim"
    return df


def _economy_status(log_rounds: list, eco_rounds: list, eco_downloaded: bool) -> str:
    """Confere a aba Economy contra a aba 2D Replay do mesmo mapa."""
    if not log_rounds:
        return "nao_usada"  # mapa sem replay: descartado de qualquer forma
    if not eco_downloaded:
        return "nao_baixada"
    if not eco_rounds:
        return "ausente"
    if len(eco_rounds) != len(log_rounds):
        return f"inconsistente: {len(eco_rounds)} rounds na Economy x {len(log_rounds)} no replay"
    bad = [r.round_number for r, e in zip(log_rounds, eco_rounds)
           if e.winner_eco is not None and r.winner is not None and e.winner_eco != r.winner]
    if bad:
        return f"inconsistente: vencedor diverge nos rounds {bad[:5]}"
    return "ok"


def process(prices_path: Path = DEFAULT_PRICES, raw_dir: Path | None = None,
            out_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Lê o cache bruto (raw_dir, padrão data/raw) e grava os CSVs (out_dir, padrão data/processed)."""
    raw_dir = RAW if raw_dir is None else raw_dir
    out_dir = OUT if out_dir is None else out_dir
    prices = load_prices(prices_path)
    matches_rows, maps_rows = [], []
    rounds_all, players_all, events_all = [], [], []

    for ev_dir in sorted(raw_dir.glob("event_*")):
        eid = int(ev_dir.name.split("_")[1])
        for md in sorted(ev_dir.glob("match_*")):
            logs = md / "logs.html"
            if not logs.exists():
                continue
            html = logs.read_text(encoding="utf-8")
            hdr = P.parse_match_header(html)
            nav = P.parse_games_nav(html)
            eco_file = md / "economy.html"
            economy = P.parse_economy(eco_file.read_text(encoding="utf-8")) if eco_file.exists() else {}
            mid = hdr["match_id"] or int(md.name.split("_")[1])
            matches_rows.append({"event_id": eid, **hdr, "match_id": mid,
                                 "mapas_listados": len(nav),
                                 "mapas_jogados": sum(g["played"] for g in nav)})
            if not nav:
                maps_rows.append({"event_id": eid, "match_id": mid, "game_id": None, "map_order": None,
                                  "map_name": None, "played": None, "status": "sem_mapas",
                                  "status_detail": "partida sem lista de mapas (W.O.?)", "n_rounds": 0})
            for g in nav:
                row = {"event_id": eid, "match_id": mid, **g, "n_rounds": 0}
                if not g["played"]:
                    row.update(status=P.MAP_STATUS_NOT_PLAYED,
                               status_detail="mapa não jogado (série decidida antes)")
                    maps_rows.append(row)
                    continue
                gf = md / f"game_{g['game_id']}.html"
                if not gf.exists():
                    row.update(status="nao_baixado", status_detail="rode 'coletar' novamente")
                    maps_rows.append(row)
                    continue
                gl = P.parse_game_logs(gf.read_text(encoding="utf-8"), g["game_id"])
                eco_rounds = economy.get(g["game_id"], [])
                row.update(status=gl.status, status_detail=gl.status_detail, n_rounds=len(gl.rounds),
                           economia_status=_economy_status(gl.rounds, eco_rounds, eco_file.exists()))
                maps_rows.append(row)
                if gl.rounds:
                    meta = {"event_id": eid, "match_id": mid, "map_name": g["map_name"],
                            "map_order": g["map_order"], "map_status": gl.status,
                            "economia_status": row["economia_status"]}
                    rdf = pd.DataFrame(P.as_dicts(gl.rounds))
                    if eco_rounds:
                        rdf = rdf.merge(pd.DataFrame(P.as_dicts(eco_rounds)),
                                        on=["game_id", "round_number"], how="left")
                    rounds_all.append(rdf.assign(**meta))
                    players_all.append(pd.DataFrame(P.as_dicts(gl.players)).assign(**meta))
                    events_all.append(pd.DataFrame(P.as_dicts(gl.events)).assign(**meta))

    partidas = pd.DataFrame(matches_rows)
    mapas = pd.DataFrame(maps_rows)
    out = {"partidas": partidas, "mapas": mapas}

    if rounds_all:
        R = pd.concat(rounds_all, ignore_index=True)
        Pl = pd.concat(players_all, ignore_index=True)
        E = pd.concat(events_all, ignore_index=True)
        meta_cols = ["event_id", "match_id", "map_name", "map_order", "map_status"]
        T = team_round_table(R, Pl.drop(columns=meta_cols), E.drop(columns=meta_cols), prices)
        cols_match = ["match_id", "event_name", "event_series", "date_utc", "patch", "team1", "team2"]
        T = T.merge(partidas[cols_match], on="match_id", how="left")
        T["tabela_precos"] = Path(prices_path).name
        lead = ["event_id", "event_name", "event_series", "match_id", "date_utc", "patch", "game_id",
                "map_order", "map_name", "map_status", "team1", "team2", "round_number", "half",
                "is_pistol_round", "is_overtime", "atk_team", "t1_side", "t2_side", "winner",
                "outcome_t1", "win_type", "team1_score_after", "team2_score_after"]
        T = T[lead + [c for c in T.columns if c not in lead]]
        out["rounds"] = T
        out["jogadores_rounds"] = player_loadout(Pl, prices)
        out["eventos_round"] = E

    if len(mapas):
        cov = (
            mapas.assign(n=1)
            .pivot_table(index="event_id", columns="status", values="n", aggfunc="sum", fill_value=0)
            .reset_index()
        )
        for c in (P.MAP_STATUS_OK, P.MAP_STATUS_NO_REPLAY, P.MAP_STATUS_NOT_PLAYED,
                  P.MAP_STATUS_INCONSISTENT, P.MAP_STATUS_NOT_RENDERED, "nao_baixado"):
            if c not in cov:
                cov[c] = 0
        cov["mapas_jogados"] = cov.drop(columns=["event_id", P.MAP_STATUS_NOT_PLAYED]).sum(axis=1)
        cov["pct_jogados_com_replay"] = (100 * cov[P.MAP_STATUS_OK] / cov["mapas_jogados"]).round(1)
        names = partidas.groupby("event_id")["event_name"].first()
        n_part = partidas.groupby("event_id").size().rename("partidas")
        cov = cov.merge(names, on="event_id", how="left").merge(n_part, on="event_id", how="left")
        out["cobertura_por_evento"] = cov

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        df.to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8")
    return out


# ----------------------------------------------------------------------------
# amostra: CSVs gerados a partir das páginas reais salvas em tests/fixtures
# ----------------------------------------------------------------------------

FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE_OUT = ROOT / "data" / "amostra"

# como cada página salva entra no cache simulado (evento/partida/arquivo)
SAMPLE_LAYOUT = {
    "event_2766/match_754732/logs.html": "match_754732_game_283271_logs.html",
    "event_2766/match_754732/game_283271.html": "match_754732_game_283271_logs.html",
    "event_2766/match_754732/game_283272.html": "match_754732_game_283272_logs.html",
    "event_2766/match_754732/economy.html": "match_754732_economy.html",
    "event_3105/match_748619/logs.html": "match_748619_noreplay_logs.html",
    "event_3105/match_748619/game_282202.html": "match_748619_noreplay_logs.html",
}


def build_sample(out_dir: Path = SAMPLE_OUT, prices_path: Path = DEFAULT_PRICES) -> dict[str, pd.DataFrame]:
    """Gera os mesmos CSVs da coleta real, sem acessar o site.

    Usa duas partidas reais salvas em tests/fixtures:
    * NRG vs T1 (Champions 2026): Lotus e Summit com replay; Abyss não jogado.
    * BLG vs XLG (China Evolution Series): Summit jogado SEM replay; Haven não
      baixado de propósito (aparece como 'nao_baixado'); Sunset não jogado.
    """
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        for dest, src in SAMPLE_LAYOUT.items():
            p = raw / dest
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / src, p)
        return process(prices_path, raw_dir=raw, out_dir=out_dir)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m vlr.crawl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-delay", type=float, default=3.0, help="intervalo mínimo entre requisições (s)")
    ap.add_argument("--max-delay", type=float, default=6.0, help="intervalo máximo entre requisições (s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("listar-eventos", help="lista eventos concluídos do tier 60 (todas as páginas)")
    s1.add_argument("--paginas", type=int, default=None,
                    help="limita às N primeiras páginas (padrão: todas)")
    s1.add_argument("--atualizar", action="store_true", help="ignora o cache da lista de eventos")

    s2 = sub.add_parser("sondar", help="testa a 1ª e a última partida de cada evento para ver se há 2D replay")
    s2.add_argument("--eventos", type=int, nargs="+", default=None,
                    help="IDs dos eventos (padrão: todos os concluídos, de todas as páginas)")
    s2.add_argument("--paginas", type=int, default=None, help="sem --eventos: limita às N primeiras páginas")

    s3 = sub.add_parser("coletar", help="baixa partidas e mapas dos eventos e gera os CSVs")
    s3.add_argument("--eventos", type=int, nargs="+", required=True)
    s3.add_argument("--max-partidas", type=int, default=None, help="limite por evento (para teste)")
    s3.add_argument("--sem-processar", action="store_true", help="só baixa; não gera os CSVs ao final")
    s3.add_argument("--precos", type=Path, default=DEFAULT_PRICES)

    s4 = sub.add_parser("processar", help="gera os CSVs a partir do cache")
    s4.add_argument("--precos", type=Path, default=DEFAULT_PRICES)

    s5 = sub.add_parser("amostra", help="gera CSVs de exemplo a partir das páginas salvas em tests/fixtures (sem rede)")
    s5.add_argument("--precos", type=Path, default=DEFAULT_PRICES)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    f = Fetcher(a.min_delay, a.max_delay)

    if a.cmd == "listar-eventos":
        df = list_events(f, a.paginas, a.atualizar)
        OUT.mkdir(parents=True, exist_ok=True)
        df.to_csv(OUT / "eventos_concluidos.csv", index=False)
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(df[["event_id", "event_name", "dates", "region"]].to_string(index=False))
    elif a.cmd == "sondar":
        events = a.eventos if a.eventos else list_events(f, a.paginas)
        df = probe(f, events)
        OUT.mkdir(parents=True, exist_ok=True)
        df.to_csv(OUT / "sondagem_replay.csv", index=False, encoding="utf-8")
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(df.to_string(index=False))
        print(f"\n-> data/processed/sondagem_replay.csv ({f.n_requests} requisições)")
    elif a.cmd == "coletar":
        collect(f, a.eventos, a.max_partidas)
        log.info("requisições feitas: %d", f.n_requests)
        if not a.sem_processar:
            _print_summary(process(a.precos), "data/processed")
    elif a.cmd == "processar":
        _print_summary(process(a.precos), "data/processed")
    elif a.cmd == "amostra":
        _print_summary(build_sample(prices_path=a.precos), "data/amostra")


def _print_summary(out: dict[str, pd.DataFrame], folder: str):
    for name, df in out.items():
        print(f"{name:24s} {len(df):7d} linhas -> {folder}/{name}.csv")
    if "cobertura_por_evento" in out:
        with pd.option_context("display.width", 200):
            print(out["cobertura_por_evento"].to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
