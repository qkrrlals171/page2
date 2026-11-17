"""플레이별 데이터 → 득점 기대값 표(RE24) → 결과별 득점 가치(선형 가중치).

가중치는 해마다 거의 변하지 않으므로 지난 시즌 데이터로 1년에 한 번 만들면 된다.
여기서 만드는 값은 '아웃 대비 득점 가치'까지이고, 시즌별 wOBA 스케일은
stats.league_context()에서 그 시즌 리그 출루율에 맞춰 붙인다.

데이터: slothman3878/kbo_playbyplay (Hugging Face, CC BY 4.0)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

from . import config

BASES = ["___", "1__", "_2_", "12_", "__3", "1_3", "_23", "123"]

EVENT_GROUPS = {
    "1B": ["single"],
    "2B": ["double"],
    "3B": ["triple"],
    "HR": ["home_run"],
    "BB": ["walk"],            # 데이터셋이 고의4구를 따로 구분하지 않는다
    "HBP": ["hit_by_pitch"],
    "OUT": ["field_out", "strikeout", "double_play", "triple_play", "fielders_choice"],
}
WOBA_EVENTS = ["BB", "HBP", "1B", "2B", "3B", "HR"]

COLUMNS = ["game_pk", "at_bat_number", "pitch_number", "inning", "inning_topbot",
           "outs_when_up", "on_1b", "on_2b", "on_3b", "events",
           "post_outs", "post_on_1b", "post_on_2b", "post_on_3b", "runs_scored"]


def download_pbp(season: int, dest: Path = config.PBP_DIR) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"kbo_pbp_{season}.parquet"
    if not path.exists():
        url = config.PBP_URL.format(season=season)
        with requests.get(url, timeout=120, stream=True) as r:
            r.raise_for_status()
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(path)
    return path


def load_plate_appearances(paths: list[Path]) -> pd.DataFrame:
    """공 단위 데이터를 타석 단위로 줄인다: 시작 상태, 결과, 득점, 종료 상태."""
    frames = []
    for p in paths:
        df = pd.read_parquet(p, columns=COLUMNS)
        df = df.sort_values(["game_pk", "at_bat_number", "pitch_number"])
        key = ["game_pk", "at_bat_number"]
        # first()/last()는 NaN(빈 베이스)을 건너뛰므로 head/tail을 써야 한다.
        start = df.groupby(key, sort=False).head(1).set_index(key)
        end = df.groupby(key, sort=False).tail(1).set_index(key)
        pa = start[["inning", "inning_topbot", "outs_when_up", "on_1b", "on_2b", "on_3b"]].copy()
        pa["events"] = end["events"]
        pa["runs"] = end["runs_scored"].fillna(0)   # 타석 전체 득점(행마다 같은 값)
        pa["post_outs"] = end["post_outs"]
        for b in ("1b", "2b", "3b"):
            pa[f"post_on_{b}"] = end[f"post_on_{b}"]
        pa["file"] = p.name
        frames.append(pa.reset_index())
    pa = pd.concat(frames, ignore_index=True)
    pa["base"] = _base_code(pa, "on_")
    pa["post_base"] = _base_code(pa, "post_on_")
    return pa


def _base_code(pa: pd.DataFrame, prefix: str) -> pd.Series:
    return (pa[f"{prefix}1b"].notna().astype(int)
            + 2 * pa[f"{prefix}2b"].notna().astype(int)
            + 4 * pa[f"{prefix}3b"].notna().astype(int))


def run_expectancy(pa: pd.DataFrame) -> pd.DataFrame:
    """24개 상황별로 '그 이닝이 끝날 때까지 평균 득점'."""
    half = ["game_pk", "inning", "inning_topbot"]
    pa = pa.sort_values(["game_pk", "at_bat_number"]).copy()
    pa["runs_to_end"] = pa.groupby(half)["runs"].transform(lambda s: s[::-1].cumsum()[::-1])
    complete = pa.groupby(half)["post_outs"].transform("max") >= 3          # 끝내기 등 미완료 이닝 제외
    not_walkoff_zone = ~((pa["inning"] >= 9) & (pa["inning_topbot"] == "bot"))
    use = pa[complete & not_walkoff_zone]
    re = use.groupby(["base", "outs_when_up"])["runs_to_end"].mean().unstack("outs_when_up")
    re = re.reindex(range(8))
    re.index = BASES
    re.columns = [int(c) for c in re.columns]
    return re


def linear_weights(pa: pd.DataFrame, re: pd.DataFrame) -> dict[str, float]:
    """결과별 평균 득점 가치 = RE(종료) − RE(시작) + 그 타석 득점."""
    re_lookup = {(b, o): re.iloc[b][o] for b in range(8) for o in range(3)}
    start = [re_lookup[(b, int(o))] for b, o in zip(pa["base"], pa["outs_when_up"])]
    end = [0.0 if po >= 3 else re_lookup[(b, int(po))]
           for b, po in zip(pa["post_base"], pa["post_outs"].fillna(3))]
    rv = pd.Series(end, index=pa.index) - pd.Series(start, index=pa.index) + pa["runs"]
    out = {}
    for group, events in EVENT_GROUPS.items():
        mask = pa["events"].isin(events)
        out[group] = float(rv[mask].mean())
    return out


def build(seasons: list[int], path: Path = config.WEIGHTS_PATH, log=print) -> dict:
    paths = []
    for s in seasons:
        log(f"플레이별 데이터 {s} 받는 중…")
        paths.append(download_pbp(s))
    pa = load_plate_appearances(paths)
    re = run_expectancy(pa)
    rv = linear_weights(pa, re)
    result = {
        "seasons": seasons,
        "source": "slothman3878/kbo_playbyplay (Hugging Face, CC BY 4.0)",
        "plate_appearances": int(len(pa)),
        "run_expectancy": {str(o): {b: round(float(re.loc[b, o]), 4) for b in BASES} for o in range(3)},
        "run_values": {k: round(v, 4) for k, v in rv.items()},
        # wOBA 가중치의 '스케일 전' 값: 아웃을 0으로 옮긴 득점 가치
        "weights_above_out": {k: round(rv[k] - rv["OUT"], 4) for k in WOBA_EVENTS},
        "event_counts": {g: int(pa["events"].isin(ev).sum()) for g, ev in EVENT_GROUPS.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load(path: Path = config.WEIGHTS_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} 가 없습니다. 먼저 `python -m kbostats build-weights` 를 실행하세요.")
    return json.loads(path.read_text(encoding="utf-8"))
