"""계산 결과를 정적 HTML 한 장(site/index.html)으로 만든다.

데이터는 페이지 안에 JSON으로 들어가고, 정렬·필터는 브라우저에서 처리한다.
서버 없이 파일만 올리면 되므로 GitHub Pages 같은 정적 호스팅에 그대로 쓸 수 있다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import config

# (키, 표시 이름, 형식, 그룹, 낮을수록 좋은가, 설명)
# 그룹: base(항상 표시) · key(항상 표시, 강조) · classic · saber
BATTING_COLUMNS = [
    ("name", "선수", "text", "base", False, ""),
    ("team", "팀", "text", "base", False, ""),
    ("wRC+", "wRC+", "int", "key", False, "구장 보정 조정 득점 창출력 (100 = 리그 평균)"),
    ("G", "G", "int", "classic", False, "경기"),
    ("PA", "PA", "int", "classic", False, "타석"),
    ("AB", "AB", "int", "classic", False, "타수"),
    ("R", "R", "int", "classic", False, "득점"),
    ("H", "H", "int", "classic", False, "안타"),
    ("2B", "2B", "int", "classic", False, "2루타"),
    ("3B", "3B", "int", "classic", False, "3루타"),
    ("HR", "HR", "int", "classic", False, "홈런"),
    ("RBI", "RBI", "int", "classic", False, "타점"),
    ("SB", "SB", "int", "classic", False, "도루"),
    ("CS", "CS", "int", "classic", True, "도루실패"),
    ("BB", "BB", "int", "classic", False, "볼넷(고의4구 포함)"),
    ("IBB", "IBB", "int", "classic", False, "고의4구"),
    ("HBP", "HBP", "int", "classic", False, "몸에 맞는 공"),
    ("SO", "SO", "int", "classic", True, "삼진"),
    ("GDP", "GDP", "int", "classic", True, "병살타"),
    ("AVG", "AVG", "rate3", "classic", False, "타율"),
    ("OBP", "OBP", "rate3", "classic", False, "출루율"),
    ("SLG", "SLG", "rate3", "classic", False, "장타율"),
    ("OPS", "OPS", "rate3", "classic", False, "출루율 + 장타율"),
    ("ISO", "ISO", "rate3", "saber", False, "순수 장타력: SLG − AVG"),
    ("BABIP", "BABIP", "rate3", "saber", False, "인플레이 타구 타율"),
    ("BB%", "BB%", "pct1", "saber", False, "타석당 볼넷 비율"),
    ("K%", "K%", "pct1", "saber", True, "타석당 삼진 비율"),
    ("BB/K", "BB/K", "dec2", "saber", False, "볼넷 / 삼진"),
    ("wOBA", "wOBA", "rate3", "saber", False, "가중 출루율 (리그 평균 = 리그 출루율)"),
    ("wRAA", "wRAA", "dec1", "saber", False, "리그 평균 타자 대비 득점 기여"),
    ("wRC", "wRC", "dec1", "saber", False, "창출한 득점"),
    ("OPS+", "OPS+", "int", "saber", False, "구장 보정 조정 OPS (100 = 리그 평균)"),
]

PITCHING_COLUMNS = [
    ("name", "선수", "text", "base", False, ""),
    ("team", "팀", "text", "base", False, ""),
    ("FIP", "FIP", "dec2", "key", True, "수비 무관 평균자책점"),
    ("G", "G", "int", "classic", False, "경기"),
    ("W", "W", "int", "classic", False, "승"),
    ("L", "L", "int", "classic", True, "패"),
    ("SV", "SV", "int", "classic", False, "세이브"),
    ("HLD", "HLD", "int", "classic", False, "홀드"),
    ("QS", "QS", "int", "classic", False, "퀄리티 스타트"),
    ("IP", "IP", "ip", "classic", False, "이닝"),
    ("H", "H", "int", "classic", True, "피안타"),
    ("HR", "HR", "int", "classic", True, "피홈런"),
    ("BB", "BB", "int", "classic", True, "볼넷"),
    ("HBP", "HBP", "int", "classic", True, "몸에 맞는 공"),
    ("SO", "SO", "int", "classic", False, "탈삼진"),
    ("R", "R", "int", "classic", True, "실점"),
    ("ER", "ER", "int", "classic", True, "자책점"),
    ("ERA", "ERA", "dec2", "classic", True, "평균자책점"),
    ("WHIP", "WHIP", "dec2", "classic", True, "이닝당 출루 허용"),
    ("K/9", "K/9", "dec2", "saber", False, "9이닝당 탈삼진"),
    ("BB/9", "BB/9", "dec2", "saber", True, "9이닝당 볼넷"),
    ("HR/9", "HR/9", "dec2", "saber", True, "9이닝당 피홈런"),
    ("K%", "K%", "pct1", "saber", False, "상대 타자당 탈삼진 비율"),
    ("BB%", "BB%", "pct1", "saber", True, "상대 타자당 볼넷 비율"),
    ("K-BB%", "K-BB%", "pct1", "saber", False, "K% − BB%"),
    ("BABIP", "BABIP", "rate3", "saber", True, "인플레이 타구 피안타율"),
    ("LOB%", "LOB%", "pct1", "saber", False, "잔루 처리율"),
    ("ERA-", "ERA-", "int", "saber", True, "구장 보정 ERA (100 = 리그 평균, 낮을수록 좋음)"),
    ("FIP-", "FIP-", "int", "saber", True, "구장 보정 FIP (100 = 리그 평균, 낮을수록 좋음)"),
]


def _columns(spec):
    return [{"key": k, "label": lab, "fmt": f, "group": g, "low": low, "title": t}
            for k, lab, f, g, low, t in spec]


def _clean(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if isinstance(v, float):
        return round(v, 5)
    if hasattr(v, "item"):   # numpy 스칼라
        return _clean(v.item())
    return v


def _records(df: pd.DataFrame, spec, extra=("qualified", "player_id")) -> list[dict]:
    keys = [k for k, *_ in spec] + [e for e in extra if e in df.columns]
    return [{k: _clean(r[k]) for k in keys if k in df.columns} for _, r in df.iterrows()]


def render(season: int, as_of: str, batting: pd.DataFrame, pitching: pd.DataFrame,
           context: dict, weights: dict, parks: dict | None,
           out: Path = config.SITE_DIR / "index.html") -> Path:
    payload = {
        "season": season,
        "as_of": as_of,
        "generated_at": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST"),
        "batting": {"columns": _columns(BATTING_COLUMNS), "rows": _records(batting, BATTING_COLUMNS)},
        "pitching": {"columns": _columns(PITCHING_COLUMNS), "rows": _records(pitching, PITCHING_COLUMNS)},
        "context": context,
        "weights": weights,
        "parks": parks,
    }
    data = json.dumps(payload, ensure_ascii=False, default=_clean).replace("</", "<\\/")
    html = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace("__DATA__", data).replace("__SEASON__", str(season))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
