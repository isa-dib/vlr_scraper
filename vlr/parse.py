"""Parsers puros (HTML -> dicts) para as páginas do vlr.gg usadas no TCC.

Nenhuma função aqui faz requisição: todas recebem o HTML já baixado.
Isso permite reprocessar o cache inteiro sem tocar no site.

Estrutura confirmada nas páginas reais em out/2026 (ver tests/fixtures):

* Lista de eventos ............ a.event-item  (+ .event-item-desc-item-status)
* Partidas de um evento ....... a.match-item  (+ .ml-status)
* Navegação de mapas .......... .vm-stats-gamesnav-item[data-game-id][data-disabled]
    - data-disabled="1"  -> mapa NÃO JOGADO (ex.: 3º mapa de uma série 2:0)
* Conteúdo de um mapa ......... .vm-stats-game[data-game-id]
    - .lg-empty "No 2D replay for this map"   -> mapa jogado, SEM replay
    - .lg-empty "Loading…" / classe js-lg-lazy -> mapa não renderizado nesta requisição
    - data-game-id="all" + "No 2D replay available for this match"
      é a aba "All Maps" (agregada) e NUNCA indica falta de replay de um mapa.
* Faixa de rounds ............. .js-lg-round[data-round]  (classe mod-won-atk/def, ícone do tipo de vitória)
* Detalhe do round ............ .lg-round[data-round]
    - .lg-band-team.mod-t1/.mod-t2  -> placar APÓS o round e lado (mod-atk/mod-def)
    - .lg-mir-row                   -> jogador, créditos restantes, gasto, armadura, arma
    - .lg-map[data-map]             -> JSON com atacante, jogadores (id -> [time, agente, nick])
                                       e eventos (kill / plant / defuse)
* Aba Economy (?tab=economy) ... renderiza todos os mapas da série de uma vez
    - .ecr-bank.mod-t1/.mod-t2 [title="NRG 0.2k"] -> banco do time após a compra
    - .ecc-c.mod-t1/.mod-t2 [title="21.6k"]       -> loadout do time calculado pelo vlr.gg
    - .ecc-sq.mod-bp/.mod-b1/.mod-b2/.mod-b3      -> Pistol / Eco / Half buy / Full buy
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# utilitários
# ----------------------------------------------------------------------------

_DIGITS = re.compile(r"\d+")


def soup_of(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml ausente
        return BeautifulSoup(html, "html.parser")


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el is not None else ""


def parse_credits(text: str) -> Optional[int]:
    """'2,200' -> 2200 ; '−4,250' (sinal unicode) -> 4250 ; '' -> None."""
    if text is None:
        return None
    digits = "".join(_DIGITS.findall(text))
    return int(digits) if digits else None


def _classes(el) -> list[str]:
    c = el.get("class") or []
    return c if isinstance(c, list) else c.split()


def _href_id(href: str) -> Optional[int]:
    m = re.match(r"^/(?:event/(?:matches/)?)?(\d+)/", href or "")
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# lista de eventos  (/events/?tier=60&page=N)
# ----------------------------------------------------------------------------

def parse_events_list(html: str) -> list[dict]:
    s = soup_of(html)
    out = []
    for a in s.select("a.event-item"):
        href = a.get("href", "")
        status_el = a.select_one(".event-item-desc-item-status")
        dates_el = a.select_one(".event-item-desc-item.mod-dates")
        flag = a.select_one(".event-item-desc-item.mod-location i.flag")
        region = None
        if flag is not None:
            region = next((c[4:] for c in _classes(flag) if c.startswith("mod-")), None)
        out.append(
            {
                "event_id": _href_id(href),
                "event_slug": href.rstrip("/").split("/")[-1],
                "event_name": _txt(a.select_one(".event-item-title")),
                "status": _txt(status_el).lower(),
                "dates": _txt(dates_el).replace("Dates", "").strip(),
                "region": region,
                "event_url": href,
            }
        )
    return out


def parse_events_last_page(html: str) -> int:
    s = soup_of(html)
    pages = [int(t) for t in (_txt(x) for x in s.select(".action-container-pages a, .action-container-pages span")) if t.isdigit()]
    return max(pages) if pages else 1


# ----------------------------------------------------------------------------
# partidas de um evento  (/event/matches/<id>/<slug>/?series_id=all)
# ----------------------------------------------------------------------------

def parse_event_matches(html: str) -> list[dict]:
    s = soup_of(html)
    out = []
    for a in s.select("a.match-item"):
        href = a.get("href", "")
        teams = [_txt(t.select_one(".match-item-vs-team-name")) for t in a.select(".match-item-vs-team")]
        scores = [_txt(t.select_one(".match-item-vs-team-score")) for t in a.select(".match-item-vs-team")]
        out.append(
            {
                "match_id": _href_id(href),
                "match_url": href,
                "status": _txt(a.select_one(".ml-status")).lower(),
                "team1": teams[0] if teams else None,
                "team2": teams[1] if len(teams) > 1 else None,
                "score1": scores[0] if scores else None,
                "score2": scores[1] if len(scores) > 1 else None,
                "round_label": _txt(a.select_one(".match-item-event")) or None,
            }
        )
    return out


# ----------------------------------------------------------------------------
# cabeçalho e navegação de mapas de uma partida
# ----------------------------------------------------------------------------

def parse_match_header(html: str) -> dict:
    s = soup_of(html)
    hdr = s.select_one(".match-header")
    names = [_txt(x) for x in s.select(".match-header-link-name .wf-title-med")]
    # o título pode trazer "(Nome curto)" embaixo; mantemos o texto principal
    names = [re.sub(r"\s*\(.*\)\s*$", "", n).strip() for n in names]
    date_el = s.select_one(".match-header-date .moment-tz-convert")
    patch = None
    for d in s.select(".match-header-date div"):
        m = re.search(r"Patch\s+([\d.]+)", _txt(d))
        if m:
            patch = m.group(1)
            break
    notes = [_txt(x) for x in s.select(".match-header-vs-note")]
    score_w = _txt(s.select_one(".match-header-vs-score-winner"))
    score_l = _txt(s.select_one(".match-header-vs-score-loser"))
    canonical = s.select_one('link[rel="canonical"]')
    url = canonical.get("href") if canonical else None
    vm = s.select_one(".vm-stats[data-url]")
    return {
        "match_id": _href_id((vm.get("data-url") if vm else "") or (url or "").replace("https://www.vlr.gg", "")),
        "match_url": vm.get("data-url") if vm else url,
        "event_name": _txt(hdr.select_one(".match-header-event div > div")) if hdr else None,
        "event_series": _txt(hdr.select_one(".match-header-event-series")) if hdr else None,
        "event_href": (hdr.select_one("a.match-header-event") or {}).get("href") if hdr else None,
        "date_utc": date_el.get("data-utc-ts") if date_el else None,
        "patch": patch,
        "team1": names[0] if names else None,
        "team2": names[1] if len(names) > 1 else None,
        "series_score": f"{score_w}:{score_l}" if score_w and score_l else None,
        "best_of": next((n for n in notes if re.fullmatch(r"Bo\d", n)), None),
        "veto": _txt(s.select_one(".match-header-note")) or None,
    }


def parse_games_nav(html: str) -> list[dict]:
    """Lista de mapas da série, na ordem, com o status de 'jogado'.

    data-disabled="1" significa que o mapa NÃO foi jogado (série acabou antes).
    Isso é diferente de "jogado mas sem 2D replay" — ver parse_game_logs.
    """
    s = soup_of(html)
    out = []
    for a in s.select(".vm-stats-gamesnav-item"):
        gid = a.get("data-game-id")
        if not gid or gid == "all":
            continue
        txt = _txt(a)
        m = re.match(r"^(\d+)\s+(.*)$", txt)
        out.append(
            {
                "game_id": int(gid),
                "map_order": int(m.group(1)) if m else None,
                "map_name": (m.group(2) if m else txt).strip(),
                "played": a.get("data-disabled", "0") != "1",
            }
        )
    return out


# ----------------------------------------------------------------------------
# aba 2D Replay de um mapa
# ----------------------------------------------------------------------------

MAP_STATUS_OK = "ok"
MAP_STATUS_NOT_PLAYED = "nao_jogado"          # data-disabled=1 na navegação
MAP_STATUS_NO_REPLAY = "sem_replay"           # jogado, mas "No 2D replay for this map"
MAP_STATUS_NOT_RENDERED = "nao_renderizado"   # HTML não trouxe o mapa (lazy) -> refazer requisição
MAP_STATUS_INCONSISTENT = "inconsistente"     # trouxe rounds, mas falhou alguma checagem

WIN_TYPE_BY_ICON = {"elim": "eliminacao", "boom": "spike_detonada", "defuse": "spike_defusada", "time": "tempo"}


@dataclass
class PlayerRound:
    game_id: int
    round_number: int
    team: int                 # 1 = time da esquerda (team1 do cabeçalho), 2 = direita
    player: str
    agent: Optional[str]
    side: Optional[str]       # atk / def
    credits_left: Optional[int]   # créditos restantes APÓS a fase de compra
    spent: Optional[int]          # gasto na fase de compra
    armor: Optional[str]          # "Light armor" / "Heavy armor" / "Regen Shield" / None
    weapon: Optional[str]         # título da arma principal exibida


@dataclass
class RoundEvent:
    game_id: int
    round_number: int
    order: int
    clock: Optional[str]
    type: str                  # kill / plant / defuse
    actor: Optional[str]
    actor_team: Optional[int]
    victim: Optional[str] = None
    victim_team: Optional[int] = None
    kill_kind: Optional[str] = None   # jogador / spike_ou_ambiente / fogo_amigo
    weapon: Optional[str] = None
    site: Optional[str] = None


@dataclass
class RoundRecord:
    game_id: int
    round_number: int
    team1_score_after: Optional[int]
    team2_score_after: Optional[int]
    winner: Optional[int]                 # 1 ou 2
    win_type: Optional[str]
    atk_team: Optional[int]               # qual time (1/2) atacou neste round
    winner_check_ok: bool = True          # placar x faixa de rounds concordam
    planted: bool = False
    plant_site: Optional[str] = None
    defused: bool = False


@dataclass
class GameLogs:
    game_id: int
    status: str
    status_detail: str = ""
    rounds: list = field(default_factory=list)
    players: list = field(default_factory=list)
    events: list = field(default_factory=list)


def _game_container(s: BeautifulSoup, game_id: int):
    return s.select_one(f'.vm-stats-game[data-game-id="{game_id}"]')


def active_game_id(html: str) -> Optional[int]:
    s = soup_of(html)
    g = s.select_one(".vm-stats-game.mod-active")
    if g is None:
        return None
    gid = g.get("data-game-id")
    return int(gid) if gid and gid.isdigit() else None


def parse_game_logs(html: str, game_id: int) -> GameLogs:
    s = soup_of(html)
    g = _game_container(s, game_id)
    if g is None:
        return GameLogs(game_id, MAP_STATUS_NOT_RENDERED, "container do mapa ausente")

    round_divs = g.select(".lg-round[data-round]")
    if not round_divs:
        empty = _txt(g.select_one(".lg-empty"))
        if "js-lg-lazy" in _classes(g) or empty.lower().startswith("loading"):
            return GameLogs(game_id, MAP_STATUS_NOT_RENDERED, empty or "lazy")
        if "no 2d replay" in empty.lower():
            return GameLogs(game_id, MAP_STATUS_NO_REPLAY, empty)
        return GameLogs(game_id, MAP_STATUS_INCONSISTENT, empty or "sem rounds e sem mensagem")

    # faixa de rounds (resultado + tipo de vitória)
    strip = {}
    for col in g.select(".js-lg-round[data-round]"):
        rn = int(col["data-round"])
        cls = _classes(col)
        won_side = "atk" if "mod-won-atk" in cls else "def" if "mod-won-def" in cls else None
        icon = col.select_one(".rnd-sq.mod-win img")
        icon_key = None
        if icon is not None:
            m = re.search(r"/round/(\w+)\.", icon.get("src", ""))
            icon_key = m.group(1) if m else None
        sqs = col.select(".rnd-sq")
        sq_winner = None
        for i, sq in enumerate(sqs[:2]):
            if "mod-win" in _classes(sq):
                sq_winner = i + 1
        title = col.get("title", "")
        title_win_type = title.rsplit(", ", 1)[-1] if ", " in title else None
        strip[rn] = {
            "won_side": won_side,
            "win_type": WIN_TYPE_BY_ICON.get(icon_key, title_win_type),
            "sq_winner": sq_winner,
        }

    out = GameLogs(game_id, MAP_STATUS_OK)
    problems = []
    prev = (0, 0)
    for rd in sorted(round_divs, key=lambda d: int(d["data-round"])):
        rn = int(rd["data-round"])

        # --- placar após o round e lado de cada time
        band = {}
        for t in rd.select(".lg-band-team"):
            cls = _classes(t)
            team = 1 if "mod-t1" in cls else 2 if "mod-t2" in cls else None
            side = "atk" if "mod-atk" in cls else "def" if "mod-def" in cls else None
            band[team] = {"score": parse_credits(_txt(t.select_one(".lg-band-score"))), "side": side}

        # --- JSON do mapa 2D (atacante, jogadores, eventos)
        mp = rd.select_one(".lg-map[data-map]")
        data = json.loads(mp["data-map"]) if mp is not None else {}
        players = {str(pid): v for pid, v in (data.get("players") or {}).items()}  # id -> [team, agent, name]
        name_team = {v[2]: v[0] for v in players.values()}
        atk_team = data.get("atk")
        if atk_team is None:
            atk_team = next((k for k, v in band.items() if v["side"] == "atk"), None)

        s1 = band.get(1, {}).get("score")
        s2 = band.get(2, {}).get("score")
        winner = None
        if s1 is not None and s2 is not None:
            d1, d2 = s1 - prev[0], s2 - prev[1]
            if (d1, d2) == (1, 0):
                winner = 1
            elif (d1, d2) == (0, 1):
                winner = 2
            prev = (s1, s2)

        st = strip.get(rn, {})
        winner_from_strip = None
        if st.get("won_side") and atk_team in (1, 2):
            winner_from_strip = atk_team if st["won_side"] == "atk" else 3 - atk_team
        check_ok = winner is not None and winner == winner_from_strip
        if not check_ok:
            problems.append(f"r{rn}: placar={winner} faixa={winner_from_strip}")
        if winner is None:
            winner = winner_from_strip

        rec = RoundRecord(
            game_id=game_id,
            round_number=rn,
            team1_score_after=s1,
            team2_score_after=s2,
            winner=winner,
            win_type=st.get("win_type"),
            atk_team=atk_team,
            winner_check_ok=check_ok,
        )

        # --- economia por jogador
        for team_div in rd.select(".lg-mir-team"):
            side = "atk" if "mod-atk" in _classes(team_div) else "def" if "mod-def" in _classes(team_div) else None
            for row in team_div.select(".lg-mir-row"):
                name = _txt(row.select_one(".lg-mir-name"))
                agent_img = row.select_one("img.lg-agent")
                agent = None
                if agent_img is not None:
                    m = re.search(r"\(([^)]+)\)\s*$", agent_img.get("title", ""))
                    agent = m.group(1) if m else agent_img.get("title")
                shield = row.select_one(".lg-kit-armor img.lg-kit-shield")
                gun = row.select_one("img.lg-kit-gun")
                team = name_team.get(name)
                if team is None:
                    team = 2 if "mod-right" in _classes(team_div) else 1
                out.players.append(
                    PlayerRound(
                        game_id=game_id,
                        round_number=rn,
                        team=team,
                        player=name,
                        agent=agent,
                        side=side,
                        credits_left=parse_credits(_txt(row.select_one(".lg-mir-left"))),
                        spent=parse_credits(_txt(row.select_one(".lg-kit-spent"))),
                        armor=shield.get("title") if shield is not None else None,
                        weapon=gun.get("title") if gun is not None else None,
                    )
                )

        # --- eventos (do JSON; a arma vem da linha correspondente do feed)
        feed_rows = rd.select(".lg-feed .lg-row[data-ev]")
        feed_by_ev = {int(r["data-ev"]): r for r in feed_rows}
        n_dom_kills = sum(1 for r in feed_rows if "mod-kill" in _classes(r))
        n_json_kills = 0
        for i, ev in enumerate(data.get("events") or []):
            etype = ev.get("type")
            by = str(ev.get("by")) if ev.get("by") is not None else None
            actor = players.get(by, [None, None, None])
            dom = feed_by_ev.get(i)
            weapon = None
            if dom is not None:
                src = dom.select_one(".lg-source")
                weapon = src.get("title") if src is not None else None
            if etype == "kill":
                n_json_kills += 1
                vid = str(ev.get("vid")) if ev.get("vid") is not None else None
                victim = players.get(vid, [None, None, ev.get("victim")])
                if by is None or by == vid or by not in players or (weapon or "").lower() == "spike":
                    kind = "spike_ou_ambiente"
                elif actor[0] == victim[0]:
                    kind = "fogo_amigo"
                else:
                    kind = "jogador"
                out.events.append(
                    RoundEvent(game_id, rn, i, ev.get("t"), "kill",
                               actor[2] if kind != "spike_ou_ambiente" else None,
                               actor[0] if kind != "spike_ou_ambiente" else None,
                               victim[2], victim[0], kind, weapon)
                )
            elif etype in ("plant", "defuse"):
                site = None
                if dom is not None:
                    site = _txt(dom.select_one(".lg-site")) or None
                    if site is None:
                        m = re.search(r"(?:planted|defused)\s+([A-C])\b", _txt(dom))
                        site = m.group(1) if m else None
                out.events.append(RoundEvent(game_id, rn, i, ev.get("t"), etype, actor[2], actor[0], site=site))
                if etype == "plant":
                    rec.planted, rec.plant_site = True, site
                else:
                    rec.defused = True
            else:
                out.events.append(RoundEvent(game_id, rn, i, ev.get("t"), str(etype), actor[2], actor[0]))
        if n_dom_kills != n_json_kills:
            problems.append(f"r{rn}: kills no feed={n_dom_kills} no JSON={n_json_kills}")

        out.rounds.append(rec)

    # o número de rounds detalhados tem que bater com a faixa
    if len(out.rounds) != len(strip):
        problems.append(f"rounds detalhados={len(out.rounds)} faixa={len(strip)}")
    n_players = {}
    for p in out.players:
        n_players.setdefault(p.round_number, 0)
        n_players[p.round_number] += 1
    bad = [r for r, n in n_players.items() if n != 10]
    if bad:
        problems.append(f"rounds sem 10 jogadores: {bad[:5]}")

    if problems:
        out.status = MAP_STATUS_INCONSISTENT
        out.status_detail = "; ".join(problems[:10])
    return out


# ----------------------------------------------------------------------------
# aba Economy  (/<partida>/?tab=economy) — uma requisição traz TODOS os mapas
# ----------------------------------------------------------------------------

# classe do quadrado de cada round -> tipo de compra segundo o vlr.gg
# (legenda da própria aba: Eco <10k · Half buy 10–18k · Full buy 18k+)
BUY_TYPE_BY_CLASS = {"mod-bp": "Pistol", "mod-b1": "Eco", "mod-b2": "Half-buy", "mod-b3": "Fullbuy"}


def _k_to_credits(text: str | None) -> Optional[int]:
    """'21.6k' -> 21600 ; 'NRG 0.2k' -> 200 ; None -> None."""
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*k\b", text)
    return int(round(float(m.group(1)) * 1000)) if m else None


@dataclass
class EconomyRound:
    game_id: int
    round_number: int
    t1_bank_vlr: Optional[int]       # créditos restantes do time após a compra (arredondado a 100)
    t2_bank_vlr: Optional[int]
    t1_loadout_vlr: Optional[int]    # valor de loadout do time calculado pelo vlr.gg (arredondado a 100)
    t2_loadout_vlr: Optional[int]
    t1_type_buy: Optional[str]       # Pistol / Eco / Half-buy / Fullbuy (classificação do vlr.gg)
    t2_type_buy: Optional[str]
    winner_eco: Optional[int]        # vencedor segundo a aba Economy (para conferência)


def parse_economy(html: str) -> dict[int, list[EconomyRound]]:
    """Rounds por mapa a partir da aba Economy.

    A faixa sempre tem 24 colunas; rounds não disputados vêm vazios (sem title)
    e são ignorados. A coluna 'Side swap' (mod-spacing) também é ignorada.
    """
    s = soup_of(html)
    out: dict[int, list[EconomyRound]] = {}
    for g in s.select(".vm-stats-game[data-game-id]"):
        gid = g.get("data-game-id")
        if not gid or not gid.isdigit():
            continue
        rounds = []
        rn = 0
        for col in g.select(".vlr-rounds-row-col"):
            cls = _classes(col)
            if "mod-teams" in cls or "mod-spacing" in cls:
                continue
            cells = {}
            for c in col.select(".ecc-c"):
                team = 1 if "mod-t1" in _classes(c) else 2 if "mod-t2" in _classes(c) else None
                sq = c.select_one(".ecc-sq")
                sq_cls = _classes(sq) if sq is not None else []
                cells[team] = {
                    "loadout": _k_to_credits(c.get("title")),
                    "buy": next((BUY_TYPE_BY_CLASS[k] for k in sq_cls if k in BUY_TYPE_BY_CLASS), None),
                    "win": "mod-win" in sq_cls,
                }
            banks = {}
            for b in col.select(".ecr-bank"):
                team = 1 if "mod-t1" in _classes(b) else 2 if "mod-t2" in _classes(b) else None
                banks[team] = _k_to_credits(b.get("title"))
            if not cells or all(v["loadout"] is None for v in cells.values()):
                continue  # coluna vazia (round não jogado)
            rn += 1
            win = [t for t, v in cells.items() if v["win"]]
            rounds.append(
                EconomyRound(
                    game_id=int(gid),
                    round_number=rn,
                    t1_bank_vlr=banks.get(1),
                    t2_bank_vlr=banks.get(2),
                    t1_loadout_vlr=cells.get(1, {}).get("loadout"),
                    t2_loadout_vlr=cells.get(2, {}).get("loadout"),
                    t1_type_buy=cells.get(1, {}).get("buy"),
                    t2_type_buy=cells.get(2, {}).get("buy"),
                    winner_eco=win[0] if len(win) == 1 else None,
                )
            )
        if rounds:
            out[int(gid)] = rounds
    return out


def as_dicts(items) -> list[dict]:
    return [asdict(x) for x in items]
