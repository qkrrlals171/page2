"""경기 결과(최종 점수 + 구장)로 득점 파크팩터를 만든다.

원리: 같은 팀의 '홈 경기 득점(양 팀 합)'과 '원정 경기 득점'을 비교하면
팀 전력은 양쪽에 똑같이 들어가 상쇄되고 구장 효과만 남는다.

보정
1. 원정 구장 보정: 원정 경기가 열린 구장들의 파크팩터로 다시 나눈다(반복 계산).
2. 회귀: 표본(홈 경기 수)이 적을수록 1 쪽으로 당긴다.
3. 절반 적용: 타자·투수는 경기의 절반만 홈에서 뛰므로 (1 + pf) / 2 를 쓴다.

구단이 아니라 '구장' 단위로 계산한다(잠실 = LG + 두산).
제2구장(포항·울산·청주 등) 경기는 계산에서 뺀다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config


def main_parks(games: pd.DataFrame) -> dict[tuple[int, str], str]:
    """(시즌, 팀) → 그 시즌 홈 경기를 가장 많이 치른 구장."""
    g = games[games["finished"]]
    counts = g.groupby(["season", "home", "park"]).size().reset_index(name="n")
    top = counts.sort_values("n", ascending=False).drop_duplicates(["season", "home"])
    return {(int(r.season), r.home): r.park for r in top.itertuples()}


def compute(games: pd.DataFrame, regression_games: float = config.PARK_REGRESSION_GAMES,
            iterations: int = 50) -> dict:
    g = games[games["finished"]].copy()
    g["runs"] = g["home_score"] + g["away_score"]
    main = main_parks(g)
    g = g[[main.get((int(s), h)) == p for s, h, p in zip(g["season"], g["home"], g["park"])]]

    teams_at: dict[str, set] = {}
    for (season, team), park in main.items():
        teams_at.setdefault(park, set()).add((season, team))
    parks = sorted(teams_at)

    away_key = pd.Series(list(zip(g["season"].astype(int), g["away"])), index=g.index)
    home_rpg, road_rpg, road_games, n_home = {}, {}, {}, {}
    for k in parks:
        home = g[g["park"] == k]
        road = g[away_key.map(lambda key: key in teams_at[k]) & (g["park"] != k)]
        home_rpg[k], road_rpg[k] = home["runs"].mean(), road["runs"].mean()
        road_games[k], n_home[k] = road, len(home)

    raw = {k: home_rpg[k] / road_rpg[k] for k in parks}
    pf = dict(raw)
    total = sum(n_home.values())
    for _ in range(iterations):
        env = {k: road_games[k]["park"].map(pf).mean() for k in parks}
        new = {k: raw[k] * env[k] for k in parks}
        mean = sum(new[k] * n_home[k] for k in parks) / total
        pf = {k: v / mean for k, v in new.items()}

    out = {}
    for k in parks:
        n = n_home[k]
        reg = 1 + (pf[k] - 1) * n / (n + regression_games)
        out[k] = {
            "games": n,
            "home_rpg": round(home_rpg[k], 3),
            "road_rpg": round(road_rpg[k], 3),
            "raw_ratio": round(raw[k], 4),
            "pf": round(pf[k], 4),
            "pf_regressed": round(reg, 4),
            "pf_half": round((1 + reg) / 2, 4),
            "teams": sorted({t for _, t in teams_at[k]}),
        }
    latest = int(g["season"].max())
    team_park = {t: p for (s, t), p in main.items() if s == latest}
    return {"seasons": sorted(int(s) for s in g["season"].unique()),
            "regression_games": regression_games, "parks": out, "team_park": team_park}


def build(games: pd.DataFrame, path: Path = config.PARKS_PATH, **kw) -> dict:
    result = compute(games, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load(path: Path = config.PARKS_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def team_factors(parks: dict | None) -> dict[str, float]:
    """팀 → wRC+ 등에 넣을 절반 적용 파크팩터. 파일이 없으면 빈 dict(=1.0)."""
    if not parks:
        return {}
    return {team: parks["parks"][park]["pf_half"] for team, park in parks["team_park"].items()
            if park in parks["parks"]}
