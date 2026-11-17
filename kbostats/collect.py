"""KBO 공식 기록실에서 시즌 누적 기록과 경기 결과를 받아 DataFrame으로 만든다."""

from __future__ import annotations

import re
from typing import Callable

import numpy as np
import pandas as pd

from . import config
from .kbo_client import KboClient, RecordView

Log = Callable[[str], None]

# (페이지 경로, {원래 열 이름: 저장할 열 이름})
HITTER_PAGES = [
    ("/Record/Player/HitterBasic/Basic1.aspx",
     {"G": "G", "PA": "PA", "AB": "AB", "R": "R", "H": "H", "2B": "2B", "3B": "3B", "HR": "HR",
      "TB": "TB", "RBI": "RBI", "SAC": "SAC", "SF": "SF", "AVG": "off_AVG"}),
    ("/Record/Player/HitterBasic/Basic2.aspx",
     {"BB": "BB", "IBB": "IBB", "HBP": "HBP", "SO": "SO", "GDP": "GDP",
      "OBP": "off_OBP", "SLG": "off_SLG"}),
    ("/Record/Player/Runner/Basic.aspx", {"SB": "SB", "CS": "CS"}),
]

PITCHER_PAGES = [
    ("/Record/Player/PitcherBasic/Basic1.aspx",
     {"G": "G", "W": "W", "L": "L", "SV": "SV", "HLD": "HLD", "IP": "IP", "H": "H", "HR": "HR",
      "BB": "BB", "HBP": "HBP", "SO": "SO", "R": "R", "ER": "ER", "ERA": "off_ERA", "WHIP": "off_WHIP"}),
    ("/Record/Player/PitcherBasic/Basic2.aspx",
     {"CG": "CG", "SHO": "SHO", "QS": "QS", "BSV": "BSV", "TBF": "TBF", "NP": "NP",
      "2B": "2B", "3B": "3B", "SAC": "SAC", "SF": "SF", "IBB": "IBB", "WP": "WP", "BK": "BK"}),
]

TEAM_BATTING_PAGE = ("/Record/Team/Hitter/Basic1.aspx",
                     {"G": "G", "PA": "PA", "AB": "AB", "R": "R", "H": "H", "2B": "2B", "3B": "3B",
                      "HR": "HR", "SAC": "SAC", "SF": "SF"})
TEAM_PITCHING_PAGE = ("/Record/Team/Pitcher/Basic1.aspx",
                      {"G": "G", "IP": "IP", "H": "H", "HR": "HR", "BB": "BB", "HBP": "HBP",
                       "SO": "SO", "R": "R", "ER": "ER"})

TEXT_COLS = {"player_id", "name", "team"}


def parse_ip(text: str) -> float:
    """'52 1/3' → 52.333…, '2/3' → 0.666…"""
    text = (text or "").strip()
    if not text or text == "-":
        return np.nan
    whole, frac = 0.0, 0.0
    for part in text.split():
        if "/" in part:
            a, b = part.split("/")
            frac = int(a) / int(b)
        else:
            whole = float(part)
    return whole + frac


def to_number(text: str) -> float:
    text = (text or "").replace(",", "").strip()
    if text in ("", "-"):
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def _rows_to_frame(header, rows, pids, columns: dict[str, str], key_cols: dict[str, str]) -> pd.DataFrame:
    idx = {h: i for i, h in enumerate(header)}
    recs = []
    for row, pid in zip(rows, pids):
        if len(row) != len(header):
            continue
        rec = {dst: row[idx[src]] for src, dst in key_cols.items()}
        if pid is not None:
            rec["player_id"] = pid
        for src, dst in columns.items():
            raw = row[idx[src]]
            rec[dst] = parse_ip(raw) if src == "IP" else to_number(raw)
        recs.append(rec)
    return pd.DataFrame(recs)


def fetch_players(client: KboClient, season: int, pages, log: Log = print) -> pd.DataFrame:
    """페이지 여러 개를 팀별로 받아 player_id 기준으로 합친다."""
    merged: pd.DataFrame | None = None
    for path, columns in pages:
        view = RecordView(client, path).load(season)
        parts = []
        for code, label in view.teams():
            t = view.table(code)
            parts.append(_rows_to_frame(t.header, t.rows, t.player_ids, columns,
                                        {"선수명": "name", "팀명": "team"}))
            log(f"  {path.split('/Record/')[1]:<34} {label:<4} {len(t.rows):>3}명")
        df = pd.concat(parts, ignore_index=True).drop_duplicates("player_id")
        if merged is None:
            merged = df
        else:
            merged = merged.merge(df, on="player_id", how="outer", suffixes=("", "_dup"))
            for c in ("name", "team"):
                merged[c] = merged[c].fillna(merged.pop(f"{c}_dup"))
    num = [c for c in merged.columns if c not in TEXT_COLS and not c.startswith("off_")]
    merged[num] = merged[num].fillna(0)
    return merged.reset_index(drop=True)


def fetch_teams(client: KboClient, season: int, page) -> pd.DataFrame:
    path, columns = page
    t = RecordView(client, path).load(season).table()
    return _rows_to_frame(t.header, t.rows, [None] * len(t.rows), columns, {"팀명": "team"})


def fetch_hitters(client, season, log: Log = print):
    return fetch_players(client, season, HITTER_PAGES, log)


def fetch_pitchers(client, season, log: Log = print):
    return fetch_players(client, season, PITCHER_PAGES, log)


def fetch_team_batting(client, season):
    return fetch_teams(client, season, TEAM_BATTING_PAGE)


def fetch_team_pitching(client, season):
    return fetch_teams(client, season, TEAM_PITCHING_PAGE)


# --- 경기 일정·결과 --------------------------------------------------------

SCHEDULE_PAGE = config.KBO_BASE + "/Schedule/Schedule.aspx"
SCHEDULE_API = config.KBO_BASE + "/ws/Schedule.asmx/GetScheduleList"


def fetch_schedule(client: KboClient, season: int, months=range(3, 12)) -> pd.DataFrame:
    """정규시즌 경기 목록: 날짜, 원정, 홈, 점수, 구장, 비고(우천취소 등), gameId."""
    client.get(SCHEDULE_PAGE)   # 세션 쿠키
    recs = []
    for month in months:
        data = client.ajax_post(SCHEDULE_API, {
            "leId": "1", "srIdList": config.SCHEDULE_SERIES, "seasonId": str(season),
            "gameMonth": f"{month:02d}", "teamId": ""}, referer=SCHEDULE_PAGE)
        day = None
        for r in data.get("rows", []):
            cells = r.get("row", [])
            by_class = {c.get("Class"): c.get("Text", "") for c in cells}
            if "day" in by_class:
                m = re.match(r"(\d{2})\.(\d{2})", by_class["day"])
                day = f"{season}-{m.group(1)}-{m.group(2)}" if m else None
            if "play" not in by_class or day is None:
                continue
            spans = re.findall(r"<span[^>]*>([^<]*)</span>", by_class["play"])
            if len(spans) < 3:
                continue
            away, home = spans[0], spans[-1]
            away_score = home_score = np.nan
            if len(spans) == 5 and spans[1].isdigit() and spans[3].isdigit():
                away_score, home_score = int(spans[1]), int(spans[3])
            gid = re.search(r"gameId=(\w+)", by_class.get("relay", ""))
            recs.append({
                "date": day, "away": away, "home": home,
                "away_score": away_score, "home_score": home_score,
                "park": re.sub(r"<[^>]+>", "", cells[-2].get("Text", "")).strip(),
                "note": re.sub(r"<[^>]+>", "", cells[-1].get("Text", "")).strip(),
                "game_id": gid.group(1) if gid else None,
            })
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df["season"] = season
    df["finished"] = df["home_score"].notna() & df["note"].isin(["-", ""])
    return df.drop_duplicates(["date", "away", "home", "game_id"]).reset_index(drop=True)
