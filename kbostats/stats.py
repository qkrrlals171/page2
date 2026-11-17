"""시즌 누적 기록 → 클래식 스탯 + 세이버 스탯.

모든 함수는 순수 계산이라 네트워크 없이 테스트할 수 있다.

타자 세이버 공식 (FanGraphs 방식, 파크팩터는 절반 적용 값)
  wOBA  = (wBB·uBB + wHBP·HBP + w1B·1B + w2B·2B + w3B·3B + wHR·HR) / (AB + BB − IBB + SF + HBP)
  scale = 리그 출루율 / 스케일 전 리그 wOBA       → 리그 wOBA = 리그 출루율
  wRAA  = (wOBA − 리그 wOBA) / scale × PA
  wRC   = (wRAA / PA + 리그 R/PA) × PA
  wRC+  = ((wRAA/PA + 리그 R/PA) + (리그 R/PA − PF·리그 R/PA)) / 리그 R/PA × 100

투수 세이버 공식
  FIP   = (13·HR + 3·(BB + HBP) − 2·SO) / IP + 상수,  상수 = 리그 ERA − 리그 (13HR + 3(BB+HBP) − 2SO)/IP
  ERA-  = 100 × (ERA / PF) / 리그 ERA,   FIP- 도 같은 방식
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

WOBA_EVENTS = ["BB", "HBP", "1B", "2B", "3B", "HR"]


def _div(a, b):
    """0으로 나누면 NaN."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(b != 0, a / np.where(b == 0, 1, b), np.nan)
    return out


# --- 리그 기준값 ------------------------------------------------------------

def batting_context(bat: pd.DataFrame, team_bat: pd.DataFrame, weights: dict) -> dict:
    t = bat[["PA", "AB", "H", "2B", "3B", "HR", "BB", "IBB", "HBP", "SF", "SO"]].sum()
    singles = t.H - t["2B"] - t["3B"] - t.HR
    counts = {"BB": t.BB - t.IBB, "HBP": t.HBP, "1B": singles, "2B": t["2B"], "3B": t["3B"], "HR": t.HR}
    woba_den = t.AB + t.BB - t.IBB + t.SF + t.HBP
    lg_obp = (t.H + t.BB + t.HBP) / (t.AB + t.BB + t.HBP + t.SF)
    lg_slg = (singles + 2 * t["2B"] + 3 * t["3B"] + 4 * t.HR) / t.AB
    w_out = weights["weights_above_out"]
    raw = sum(w_out[e] * counts[e] for e in WOBA_EVENTS) / woba_den
    scale = lg_obp / raw
    runs = float(team_bat["R"].sum())
    pa = float(team_bat["PA"].sum()) if "PA" in team_bat else float(t.PA)
    return {
        "lg_avg": float(t.H / t.AB),
        "lg_obp": float(lg_obp),
        "lg_slg": float(lg_slg),
        "lg_woba": float(lg_obp),
        "woba_scale": float(scale),
        "woba_weights": {e: round(float(w_out[e] * scale), 4) for e in WOBA_EVENTS},
        "lg_r_pa": runs / pa,
        "lg_runs": runs,
        "lg_pa": pa,
        "lg_k_pct": float(t.SO / t.PA),
        "lg_bb_pct": float(t.BB / t.PA),
        "team_games": {r.team: int(r.G) for r in team_bat.itertuples()},
    }


def pitching_context(pit: pd.DataFrame) -> dict:
    t = pit[["IP", "ER", "R", "HR", "BB", "HBP", "SO", "TBF", "H"]].sum()
    lg_era = 9 * t.ER / t.IP
    fip_core = (13 * t.HR + 3 * (t.BB + t.HBP) - 2 * t.SO) / t.IP
    return {
        "lg_era": float(lg_era),
        "lg_ra9": float(9 * t.R / t.IP),
        "fip_constant": float(lg_era - fip_core),
        "lg_k_pct": float(t.SO / t.TBF),
        "lg_bb_pct": float(t.BB / t.TBF),
        "lg_hr9": float(9 * t.HR / t.IP),
        "lg_ip": float(t.IP),
    }


# --- 타자 -------------------------------------------------------------------

def batting(bat: pd.DataFrame, ctx: dict, park_half: dict[str, float] | None = None) -> pd.DataFrame:
    d = bat.copy()
    park_half = park_half or {}
    d["1B"] = d.H - d["2B"] - d["3B"] - d.HR
    if "TB" not in d or d["TB"].isna().all():
        d["TB"] = d["1B"] + 2 * d["2B"] + 3 * d["3B"] + 4 * d.HR

    # 클래식
    d["AVG"] = _div(d.H, d.AB)
    d["OBP"] = _div(d.H + d.BB + d.HBP, d.AB + d.BB + d.HBP + d.SF)
    d["SLG"] = _div(d.TB, d.AB)
    d["OPS"] = d.OBP + d.SLG

    # 세이버
    d["ISO"] = d.SLG - d.AVG
    d["BABIP"] = _div(d.H - d.HR, d.AB - d.SO - d.HR + d.SF)
    d["BB%"] = _div(d.BB, d.PA)
    d["K%"] = _div(d.SO, d.PA)
    d["BB/K"] = _div(d.BB, d.SO)
    w = ctx["woba_weights"]
    num = (w["BB"] * (d.BB - d.IBB) + w["HBP"] * d.HBP + w["1B"] * d["1B"]
           + w["2B"] * d["2B"] + w["3B"] * d["3B"] + w["HR"] * d.HR)
    d["wOBA"] = _div(num, d.AB + d.BB - d.IBB + d.SF + d.HBP)
    d["wRAA"] = (d.wOBA - ctx["lg_woba"]) / ctx["woba_scale"] * d.PA
    d["wRAA"] = d["wRAA"].fillna(0)
    lg = ctx["lg_r_pa"]
    d["wRC"] = d.wRAA + lg * d.PA
    d["PF"] = d.team.map(park_half).fillna(1.0)
    per_pa = _div(d.wRAA, d.PA)
    d["wRC+"] = ((per_pa + lg) + (lg - d.PF * lg)) / lg * 100
    d["OPS+"] = 100 * (d.OBP / ctx["lg_obp"] + d.SLG / ctx["lg_slg"] - 1) / d.PF
    d["SB%"] = _div(d.SB, d.SB + d.CS)

    team_g = ctx["team_games"]
    d["qualified"] = d.PA >= d.team.map(team_g).fillna(0) * config.QUALIFY_PA_PER_GAME
    return d


# --- 투수 -------------------------------------------------------------------

def pitching(pit: pd.DataFrame, ctx: dict, team_games: dict[str, int],
             park_half: dict[str, float] | None = None) -> pd.DataFrame:
    d = pit.copy()
    park_half = park_half or {}
    ip = d.IP

    # 클래식
    d["ERA"] = _div(9 * d.ER, ip)
    d["WHIP"] = _div(d.H + d.BB, ip)
    d["WPCT"] = _div(d.W, d.W + d.L)
    d["IP_display"] = ip.map(format_ip)

    # 세이버
    d["K/9"] = _div(9 * d.SO, ip)
    d["BB/9"] = _div(9 * d.BB, ip)
    d["HR/9"] = _div(9 * d.HR, ip)
    d["K%"] = _div(d.SO, d.TBF)
    d["BB%"] = _div(d.BB, d.TBF)
    d["K-BB%"] = d["K%"] - d["BB%"]
    ab = d.TBF - d.BB - d.HBP - d.SAC - d.SF
    d["BABIP"] = _div(d.H - d.HR, ab - d.SO - d.HR + d.SF)
    d["LOB%"] = _div(d.H + d.BB + d.HBP - d.R, d.H + d.BB + d.HBP - 1.4 * d.HR)
    d["FIP"] = _div(13 * d.HR + 3 * (d.BB + d.HBP) - 2 * d.SO, ip) + ctx["fip_constant"]
    d["RA9"] = _div(9 * d.R, ip)
    d["PF"] = d.team.map(park_half).fillna(1.0)
    d["ERA-"] = 100 * _div(d.ERA / d.PF, ctx["lg_era"])
    d["FIP-"] = 100 * _div(d.FIP / d.PF, ctx["lg_era"])

    d["qualified"] = ip >= d.team.map(team_games).fillna(0) * config.QUALIFY_IP_PER_GAME
    return d


def format_ip(ip: float) -> str:
    """52.333… → '52 1/3'"""
    if pd.isna(ip):
        return "-"
    whole = int(ip + 1e-9)
    thirds = int(round((ip - whole) * 3))
    if thirds == 3:
        whole, thirds = whole + 1, 0
    return f"{whole}" if thirds == 0 else (f"{whole} {thirds}/3" if whole else f"{thirds}/3")
