"""SQLite 저장.

- batting / pitching 테이블: 시즌별 최신 계산 결과(SQL로 바로 조회 가능)
- snapshots 테이블: 날짜별 기록 전체(JSON). 나중에 'wRC+ 추이' 같은 그래프에 쓸 수 있다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from . import config


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        season INTEGER, as_of TEXT, kind TEXT, data TEXT,
        PRIMARY KEY (season, as_of, kind))""")
    return con


def save(season: int, as_of: str, batting: pd.DataFrame, pitching: pd.DataFrame,
         context: dict, path: Path = config.DB_PATH) -> None:
    con = _connect(path)
    try:
        for kind, df in (("batting", batting), ("pitching", pitching)):
            table = df.assign(season=season, as_of=as_of)
            # 테이블이 없을 때 read_sql을 시도하면 pandas가 실패 처리로 rollback 하면서
            # 앞서 넣은 스냅샷까지 지워 버린다. 그래서 존재 여부를 먼저 확인한다.
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                                 (kind,)).fetchone()
            if exists:
                old = pd.read_sql(f"SELECT * FROM {kind} WHERE season != ?", con, params=(season,))
                table = pd.concat([old, table], ignore_index=True)
            table.to_sql(kind, con, if_exists="replace", index=False)
            con.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
                        (season, as_of, kind, df.to_json(orient="records", force_ascii=False)))
            con.commit()
        con.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
                    (season, as_of, "context", json.dumps(context, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


def latest_season(path: Path = config.DB_PATH) -> int:
    con = _connect(path)
    try:
        row = con.execute("SELECT MAX(season) FROM snapshots").fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        raise LookupError("저장된 기록이 없습니다. 먼저 update 를 실행하세요.")
    return int(row[0])


def latest(season: int, path: Path = config.DB_PATH) -> tuple[str, pd.DataFrame, pd.DataFrame, dict]:
    con = _connect(path)
    try:
        row = con.execute("SELECT MAX(as_of) FROM snapshots WHERE season = ?", (season,)).fetchone()
        if not row or not row[0]:
            raise LookupError(f"{season} 시즌 저장 기록이 없습니다. 먼저 update 를 실행하세요.")
        as_of = row[0]
        data = dict(con.execute("SELECT kind, data FROM snapshots WHERE season = ? AND as_of = ?",
                                (season, as_of)).fetchall())
    finally:
        con.close()
    from io import StringIO
    return (as_of, pd.read_json(StringIO(data["batting"]), dtype={"player_id": str}),
            pd.read_json(StringIO(data["pitching"]), dtype={"player_id": str}),
            json.loads(data["context"]))
