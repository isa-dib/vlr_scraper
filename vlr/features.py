"""Agregação round a round por time (camada 'silver' do pipeline).

Recebe as linhas de jogador e de eventos produzidas por vlr.parse e monta
UMA linha por (mapa, round), com colunas t1_* e t2_* para os dois times.

O tipo de compra (tN_type_buy) e o loadout do vlr.gg (tN_loadout_vlr) chegam
prontos da aba Economy e só são repassados aqui.

O que fica para a etapa de transformação (fora deste módulo, de propósito):
* win_count / loss_count / score_diff acumulados e defasados
* conversão pós-pistol e rodada bônus
* perspectiva "time A" para o modelo
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

DEFAULT_PRICES = Path(__file__).resolve().parent.parent / "config" / "precos_itens.csv"


def load_prices(path: Path | str = DEFAULT_PRICES) -> dict[str, int]:
    with open(path, newline="", encoding="utf-8") as f:
        return {r["item"].strip(): int(r["preco"]) for r in csv.DictReader(f)}


def player_loadout(players: pd.DataFrame, prices: dict[str, int]) -> pd.DataFrame:
    """Adiciona preço da arma, da armadura e loadout (arma + armadura) por jogador.

    Habilidades NÃO aparecem na página, então ficam fora do loadout.
    Itens sem preço na tabela ficam marcados em item_sem_preco para revisão.
    """
    p = players.copy()
    p["weapon_price"] = p["weapon"].map(prices)
    p["armor_price"] = p["armor"].map(prices)
    p.loc[p["armor"].isna(), "armor_price"] = 0
    p.loc[p["weapon"].isna(), "weapon_price"] = 0  # sem arma exibida
    p["item_sem_preco"] = p["weapon_price"].isna() | p["armor_price"].isna()
    p["loadout_value"] = p["weapon_price"].fillna(0) + p["armor_price"].fillna(0)
    return p


def team_round_table(rounds: pd.DataFrame, players: pd.DataFrame, events: pd.DataFrame,
                     prices: dict[str, int]) -> pd.DataFrame:
    p = player_loadout(players, prices)
    key = ["game_id", "round_number"]

    eco = (
        p.assign(
            heavy=p["armor"].eq("Heavy armor"),
            light=p["armor"].eq("Light armor"),
            regen=p["armor"].eq("Regen Shield"),
            no_armor=p["armor"].isna(),
        )
        .groupby(key + ["team"])
        .agg(
            credits_left=("credits_left", "sum"),
            spent=("spent", "sum"),
            loadout_value=("loadout_value", "sum"),
            n_players=("player", "count"),
            n_heavy=("heavy", "sum"),
            n_light=("light", "sum"),
            n_regen=("regen", "sum"),
            n_no_armor=("no_armor", "sum"),
            n_itens_sem_preco=("item_sem_preco", "sum"),
        )
        .reset_index()
    )

    k = events[events["type"] == "kill"] if len(events) else events
    if len(k):
        kills = (
            k[k["kill_kind"] == "jogador"].groupby(key + ["actor_team"]).size()
            .rename("kills").reset_index().rename(columns={"actor_team": "team"})
        )
        teamkills = (
            k[k["kill_kind"] == "fogo_amigo"].groupby(key + ["actor_team"]).size()
            .rename("teamkills").reset_index().rename(columns={"actor_team": "team"})
        )
        deaths = (
            k.groupby(key + ["victim_team"]).size()
            .rename("deaths").reset_index().rename(columns={"victim_team": "team"})
        )
        spike_deaths = (
            k[k["kill_kind"] == "spike_ou_ambiente"].groupby(key + ["victim_team"]).size()
            .rename("spike_deaths").reset_index().rename(columns={"victim_team": "team"})
        )
        for extra in (kills, teamkills, deaths, spike_deaths):
            extra["team"] = extra["team"].astype(int)
            eco = eco.merge(extra, on=key + ["team"], how="left")
    for c in ("kills", "teamkills", "deaths", "spike_deaths"):
        if c not in eco:
            eco[c] = 0
        eco[c] = eco[c].fillna(0).astype(int)

    wide = None
    for t in (1, 2):
        part = eco[eco["team"] == t].drop(columns="team").set_index(key).add_prefix(f"t{t}_")
        wide = part if wide is None else wide.join(part, how="outer")
    wide = wide.reset_index()

    out = rounds.merge(wide, on=key, how="left")
    out["t1_side"] = out["atk_team"].map({1: "atk", 2: "def"})
    out["t2_side"] = out["atk_team"].map({1: "def", 2: "atk"})
    out["outcome_t1"] = out["winner"].map({1: "Win", 2: "Loss"})
    # conferência: banco da aba Economy (arredondado a 100) x soma exata dos jogadores
    if "t1_bank_vlr" in out:
        out["banco_confere"] = (
            (out["t1_bank_vlr"] - out["t1_credits_left"]).abs().le(50)
            & (out["t2_bank_vlr"] - out["t2_credits_left"]).abs().le(50)
        )
    out["is_overtime"] = out["round_number"] > 24
    out["is_pistol_round"] = out["round_number"].isin([1, 13])
    out["half"] = out["round_number"].apply(lambda r: 1 if r <= 12 else 2 if r <= 24 else 3)
    return out
