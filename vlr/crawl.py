"""Coleta do vlr.gg (camada bruta) e processamento para Parquet.

Pode ser usado de duas formas:

* no Google Colab, pelo notebook `colab/coleta_vlr.ipynb` (que chama as funções
  de `vlr.colab`), gravando tudo numa pasta do Google Drive;
* pela linha de comando, em qualquer máquina:

    python -m vlr.crawl --dados /caminho/da/pasta amostra
    python -m vlr.crawl --dados /caminho/da/pasta listar-eventos
    python -m vlr.crawl --dados /caminho/da/pasta sondar --eventos 2766 2977
    python -m vlr.crawl --dados /caminho/da/pasta coletar --eventos 2766 --max-partidas 2
    python -m vlr.crawl --dados /caminho/da/pasta processar

Sem --dados, usa a variável de ambiente VLR_DADOS ou a pasta data/ do projeto.

A coleta é retomável: cada página baixada vira um arquivo em <dados>/raw, e o que
já existe lá não é baixado de novo.
"""
from __future__ import annotations

import argparse
import logging
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from . import parse as P
from . import storage as S
from .features import load_prices, team_round_table, player_loadout, DEFAULT_PRICES

BASE = "https://www.vlr.gg"
ROOT = S.PROJECT_ROOT

log = logging.getLogger("vlr")


# ----------------------------------------------------------------------------
# logging: terminal/célula do notebook + arquivo em <dados>/logs
# ----------------------------------------------------------------------------

def configurar_log(verbose: bool = False, arquivo: bool = True) -> Path | None:
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if not arquivo:
        return None
    dest = S.paths().logs / f"coleta_{datetime.now():%Y%m%d_%H%M%S}.log"
    fh = logging.FileHandler(dest, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return dest


def _registrar_falha(etapa: str, event_id, match_id, erro: Exception | str):
    """Grava a falha em <dados>/logs/falhas.parquet na hora (sobrevive à queda do Colab)."""
    try:
        S.anexar_tabela(
            pd.DataFrame([{"quando": datetime.now().isoformat(timespec="seconds"), "etapa": etapa,
                           "event_id": event_id, "match_id": match_id, "erro": str(erro)}]),
            S.paths().logs, "falhas",
        )
    except Exception as e:  # o registro de falhas nunca pode derrubar a coleta
        log.warning("não consegui gravar falhas.parquet: %s", e)


# ----------------------------------------------------------------------------
# HTTP educado: intervalo aleatório, retry com backoff, cache em disco
# ----------------------------------------------------------------------------

class Fetcher:
    def __init__(self, min_delay: float = 3.0, max_delay: float = 6.0, user_agent: str | None = None,
                 store: S.RawStore | None = None):
        self.min_delay, self.max_delay = min_delay, max_delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent or (
            "Mozilla/5.0 (compatible; TCC-academic-scraper/1.0; coleta para pesquisa academica)"
        )
        self.store = store or S.RawStore(S.paths().raw)
        self._last = 0.0
        self.n_requests = 0

    def _wait(self):
        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.time() - self._last
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(self, path: str, key: str, refresh: bool = False) -> str:
        """Devolve a página `path`; usa o cache `key` se já existir."""
        if not refresh and self.store.exists(key):
            return self.store.read(key)
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
                self.store.write(key, r.text)   # gravação atômica (ver storage.py)
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                wait = 30 * (attempt + 1)
                log.warning("HTTP %s em %s; aguardando %ds", r.status_code, url, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {r.status_code} em {url}")
        raise RuntimeError(f"falhou após 5 tentativas: {url}")


# ----------------------------------------------------------------------------
# chaves do cache
# ----------------------------------------------------------------------------

def events_page_key(page: int) -> str:
    return f"eventos/tier60_page_{page}.html"


def event_matches_key(eid: int) -> str:
    return f"event_{eid}/matches.html"


def match_key(eid: int, mid: int, arquivo: str) -> str:
    return f"event_{eid}/match_{mid}/{arquivo}"


# ----------------------------------------------------------------------------
# comandos
# ----------------------------------------------------------------------------

def list_events(f: Fetcher, pages: int | None = None, refresh: bool = False) -> pd.DataFrame:
    """Eventos concluídos do tier 60.

    pages=None (padrão) lê TODAS as páginas: o número da última é lido da
    paginação da primeira página. Um inteiro limita a leitura às N primeiras.
    """
    first = f.get("/events/?tier=60&page=1", events_page_key(1), refresh=refresh)
    last = P.parse_events_last_page(first)
    if pages is not None:
        last = min(last, pages)
    log.info("lendo %d página(s) de eventos", last)
    rows = [dict(r, page=1) for r in P.parse_events_list(first)]
    for page in range(2, last + 1):
        html = f.get(f"/events/?tier=60&page={page}", events_page_key(page), refresh=refresh)
        rows += [dict(r, page=page) for r in P.parse_events_list(html)]
    df = pd.DataFrame(rows).drop_duplicates("event_id")
    return df[df["status"] == "completed"].reset_index(drop=True)


def _event_matches(f: Fetcher, eid: int) -> list[dict]:
    html = f.get(f"/event/matches/{eid}/x/?series_id=all", event_matches_key(eid))
    return [m for m in P.parse_event_matches(html) if m["status"] == "completed"]


def _fetch_match(f: Fetcher, eid: int, m: dict) -> dict:
    """Baixa a aba 2D Replay de todos os mapas JOGADOS e a aba Economy de uma partida."""
    mid = m["match_id"]
    base_path = m["match_url"].rstrip("/")
    html = f.get(f"{base_path}/?tab=logs", match_key(eid, mid, "logs.html"))
    nav = P.parse_games_nav(html)
    active = P.active_game_id(html)
    fetched = 0
    if any(g["played"] for g in nav):
        # aba Economy: uma requisição traz loadout e tipo de compra de todos os mapas
        k = match_key(eid, mid, "economy.html")
        if not f.store.exists(k):
            f.get(f"{base_path}/?tab=economy", k)
            fetched += 1
    for g in nav:
        if not g["played"]:
            continue  # mapa não jogado: não existe replay a buscar
        if g["game_id"] == active:
            continue  # o mapa já vem renderizado em logs.html: não grava cópia (ver game_html)
        k = match_key(eid, mid, f"game_{g['game_id']}.html")
        if f.store.exists(k):
            continue
        f.get(f"{base_path}/?game={g['game_id']}&tab=logs", k)
        fetched += 1
    return {"match_id": mid, "maps": len(nav), "fetched": fetched}


def game_html(store: S.RawStore, mkey: str, game_id: int, logs_html: str, active: int | None) -> str | None:
    """HTML da aba 2D Replay de um mapa.

    O mapa que vem renderizado na página da partida (o 'ativo', normalmente o 1º) é lido
    direto de logs.html; os demais, de game_<id>.html. Caches antigos, que guardavam uma
    cópia em game_<id>.html também para o mapa ativo, continuam funcionando.
    """
    gkey = f"{mkey}/game_{game_id}.html"
    if store.exists(gkey):
        return store.read(gkey)
    if game_id == active:
        return logs_html
    return None


def remover_copias_duplicadas(store: S.RawStore | None = None) -> dict:
    """Apaga game_<id>.html idênticos a logs.html (cópias gravadas por versões anteriores)."""
    store = store or S.RawStore(S.paths().raw)
    n, liberado = 0, 0
    for ev in store.event_dirs():
        for md in store.match_dirs(ev):
            mkey = store.key_of(md)
            if not store.exists(f"{mkey}/logs.html"):
                continue
            logs = store.read(f"{mkey}/logs.html")
            active = P.active_game_id(logs)
            if active is None:
                continue
            for p in (md / f"game_{active}.html.gz", md / f"game_{active}.html"):
                if p.exists() and store.read(f"{mkey}/game_{active}.html") == logs:
                    liberado += p.stat().st_size
                    p.unlink()
                    n += 1
                    break
    log.info("cópias removidas: %d (%.1f MB liberados)", n, liberado / 1e6)
    return {"removidas": n, "mb_liberados": round(liberado / 1e6, 1)}


def collect(f: Fetcher, event_ids: list[int], max_matches: int | None = None) -> dict:
    """Baixa os eventos. Uma falha (de evento ou de partida) é registrada e a coleta segue."""
    n = f.store.limpar_temporarios()
    if n:
        log.info("removidos %d arquivos temporários de uma execução interrompida", n)
    resumo = {"eventos": 0, "partidas_ok": 0, "partidas_falha": 0, "eventos_falha": 0}
    for ei, eid in enumerate(event_ids, 1):
        try:
            matches = _event_matches(f, eid)
        except Exception as e:
            log.error("evento %s (%d/%d): lista de partidas falhou: %s", eid, ei, len(event_ids), e)
            _registrar_falha("lista_partidas", eid, None, e)
            resumo["eventos_falha"] += 1
            continue
        if max_matches:
            matches = matches[:max_matches]
        resumo["eventos"] += 1
        log.info("evento %s (%d/%d): %d partidas concluídas", eid, ei, len(event_ids), len(matches))
        for i, m in enumerate(matches, 1):
            try:
                info = _fetch_match(f, eid, m)
                resumo["partidas_ok"] += 1
                log.info("  [%d/%d] partida %s ok (%d mapas listados, %d requisições)",
                         i, len(matches), m["match_id"], info["maps"], info["fetched"])
            except Exception as e:  # não derruba a coleta inteira
                resumo["partidas_falha"] += 1
                log.error("  [%d/%d] partida %s falhou: %s", i, len(matches), m["match_id"], e)
                _registrar_falha("partida", eid, m["match_id"], e)
    log.info("coleta encerrada: %s | requisições: %d", resumo, f.n_requests)
    return resumo


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
    for n, eid in enumerate(ids, 1):
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
                html = f.get(f"{m['match_url'].rstrip('/')}/?tab=logs",
                             match_key(eid, m["match_id"], "logs.html"))
                if base["evento"] is None:
                    base["evento"] = P.parse_match_header(html)["event_name"]
                res.append(_probe_status(html))
            rows.append({**base, "partidas_concluidas": len(matches),
                         "primeira_partida_mapa1": res[0], "ultima_partida_mapa1": res[1]})
            log.info("sondagem %d/%d: evento %s -> %s / %s", n, len(ids), eid, res[0], res[1])
        except Exception as e:
            log.error("evento %s falhou na sondagem: %s", eid, e)
            _registrar_falha("sondagem", eid, None, e)
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
            out_dir: Path | None = None, formato: str = "parquet") -> dict[str, pd.DataFrame]:
    """Lê o cache bruto e grava as tabelas finais.

    raw_dir: padrão <dados>/raw · out_dir: padrão <dados>/processed · formato: parquet ou csv.
    """
    store = S.RawStore(S.paths().raw if raw_dir is None else raw_dir)
    out_dir = S.paths().processed if out_dir is None else Path(out_dir)
    prices = load_prices(prices_path)
    matches_rows, maps_rows = [], []
    rounds_all, players_all, events_all = [], [], []

    ev_dirs = store.event_dirs()
    for n_ev, ev_dir in enumerate(ev_dirs, 1):
        eid = int(ev_dir.name.split("_")[1])
        mdirs = store.match_dirs(ev_dir)
        log.info("processando evento %s (%d/%d, %d partidas)", eid, n_ev, len(ev_dirs), len(mdirs))
        for md in mdirs:
            mkey = store.key_of(md)
            if not store.exists(f"{mkey}/logs.html"):
                continue
            html = store.read(f"{mkey}/logs.html")
            hdr = P.parse_match_header(html)
            nav = P.parse_games_nav(html)
            active = P.active_game_id(html)
            eco_ok = store.exists(f"{mkey}/economy.html")
            economy = P.parse_economy(store.read(f"{mkey}/economy.html")) if eco_ok else {}
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
                ghtml = game_html(store, mkey, g["game_id"], html, active)
                if ghtml is None:
                    row.update(status="nao_baixado", status_detail="rode 'coletar' novamente")
                    maps_rows.append(row)
                    continue
                gl = P.parse_game_logs(ghtml, g["game_id"])
                eco_rounds = economy.get(g["game_id"], [])
                row.update(status=gl.status, status_detail=gl.status_detail, n_rounds=len(gl.rounds),
                           economia_status=_economy_status(gl.rounds, eco_rounds, eco_ok))
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
        cols_match = ["match_id", "event_name", "event_series", "date_utc", "patch", "team_a", "team_b"]
        T = T.merge(partidas[cols_match], on="match_id", how="left")
        T["tabela_precos"] = Path(prices_path).name
        lead = ["event_id", "event_name", "event_series", "match_id", "date_utc", "patch", "game_id",
                "map_order", "map_name", "map_status", "team_a", "team_b", "round_number", "half",
                "is_pistol_round", "is_overtime", "atk_team", "team_a_side", "team_b_side", "winner",
                "outcome_team_a", "win_type", "team_a_score_after", "team_b_score_after"]
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

    for name, df in out.items():
        S.salvar_tabela(df, out_dir, name, formato)
    return out


# ----------------------------------------------------------------------------
# amostra: CSVs gerados a partir das páginas reais salvas em tests/fixtures
# ----------------------------------------------------------------------------

FIXTURES = ROOT / "tests" / "fixtures"

# como cada página salva entra no cache simulado (evento/partida/arquivo)
SAMPLE_LAYOUT = {
    "event_2766/match_754732/logs.html": "match_754732_game_283271_logs.html",   # já traz Lotus
    "event_2766/match_754732/game_283272.html": "match_754732_game_283272_logs.html",
    "event_2766/match_754732/economy.html": "match_754732_economy.html",
    "event_3105/match_748619/logs.html": "match_748619_noreplay_logs.html",      # já traz Summit
}


def build_sample(out_dir: Path | None = None, prices_path: Path = DEFAULT_PRICES) -> dict[str, pd.DataFrame]:
    """Gera as mesmas tabelas da coleta real, em CSV, sem acessar o site.

    Usa duas partidas reais salvas em tests/fixtures:
    * NRG vs T1 (Champions 2026): Lotus e Summit com replay; Abyss não jogado.
    * BLG vs XLG (China Evolution Series): Summit jogado SEM replay; Haven não
      baixado de propósito (aparece como 'nao_baixado'); Sunset não jogado.
    """
    out_dir = S.paths().amostra if out_dir is None else Path(out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        for dest, src in SAMPLE_LAYOUT.items():
            p = raw / dest
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / src, p)
        return process(prices_path, raw_dir=raw, out_dir=out_dir, formato="csv")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m vlr.crawl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", type=Path, default=None,
                    help="pasta-base das saídas (padrão: $VLR_DADOS ou data/ do projeto)")
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

    s3 = sub.add_parser("coletar", help="baixa partidas e mapas dos eventos e gera as tabelas")
    s3.add_argument("--eventos", type=int, nargs="+", required=True)
    s3.add_argument("--max-partidas", type=int, default=None, help="limite por evento (para teste)")
    s3.add_argument("--sem-processar", action="store_true", help="só baixa; não gera as tabelas ao final")
    s3.add_argument("--precos", type=Path, default=DEFAULT_PRICES)

    s4 = sub.add_parser("processar", help="gera as tabelas (Parquet) a partir do cache")
    s4.add_argument("--precos", type=Path, default=DEFAULT_PRICES)

    s5 = sub.add_parser("amostra", help="gera CSVs de exemplo a partir das páginas salvas em tests/fixtures (sem rede)")
    s5.add_argument("--precos", type=Path, default=DEFAULT_PRICES)

    a = ap.parse_args(argv)
    pth = S.configurar(a.dados)
    configurar_log(a.verbose, arquivo=a.cmd in ("coletar", "sondar", "processar"))
    log.info("pasta de dados: %s", pth.base)

    if a.cmd == "amostra":
        _print_summary(build_sample(prices_path=a.precos), pth.amostra, "csv")
        return
    if a.cmd == "processar":
        _print_summary(process(a.precos), pth.processed, "parquet")
        return

    f = Fetcher(a.min_delay, a.max_delay)
    if a.cmd == "listar-eventos":
        df = list_events(f, a.paginas, a.atualizar)
        S.salvar_tabela(df, pth.processed, "eventos_concluidos")
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(df[["event_id", "event_name", "dates", "region", "page"]].to_string(index=False))
    elif a.cmd == "sondar":
        events = a.eventos if a.eventos else list_events(f, a.paginas)
        df = probe(f, events)
        dest = S.salvar_tabela(df, pth.processed, "sondagem_replay")
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(df.to_string(index=False))
        print(f"\n-> {dest} ({f.n_requests} requisições)")
    elif a.cmd == "coletar":
        try:
            collect(f, a.eventos, a.max_partidas)
        except KeyboardInterrupt:
            log.warning("coleta interrompida; gerando as tabelas com o que já está em raw/")
        finally:
            if not a.sem_processar:
                _print_summary(process(a.precos), pth.processed, "parquet")


def _print_summary(out: dict[str, pd.DataFrame], pasta: Path, ext: str):
    for name, df in out.items():
        print(f"{name:24s} {len(df):7d} linhas -> {pasta / f'{name}.{ext}'}")
    if "cobertura_por_evento" in out:
        with pd.option_context("display.width", 200):
            print(out["cobertura_por_evento"].to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
