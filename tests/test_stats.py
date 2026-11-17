"""공식이 의도한 성질을 지키는지 확인하는 테스트 (네트워크 불필요)."""

import numpy as np
import pandas as pd
import pytest

from kbostats import stats
from kbostats.collect import parse_ip, to_number

WEIGHTS = {"weights_above_out": {"BB": 0.69, "HBP": 0.71, "1B": 0.83, "2B": 1.15, "3B": 1.45, "HR": 1.73}}


def hitters():
    return pd.DataFrame([
        # 강타자 / 평균 / 약타자 / 0타석
        dict(player_id="1", name="가", team="A", G=140, PA=600, AB=500, R=100, H=170, **{"2B": 35, "3B": 2},
             HR=35, TB=0, RBI=110, SAC=0, SF=5, BB=85, IBB=10, HBP=10, SO=100, GDP=10, SB=5, CS=2),
        dict(player_id="2", name="나", team="A", G=140, PA=550, AB=480, R=70, H=130, **{"2B": 25, "3B": 3},
             HR=12, TB=0, RBI=60, SAC=5, SF=5, BB=50, IBB=2, HBP=10, SO=100, GDP=12, SB=10, CS=4),
        dict(player_id="3", name="다", team="B", G=120, PA=400, AB=360, R=35, H=80, **{"2B": 12, "3B": 1},
             HR=3, TB=0, RBI=30, SAC=10, SF=2, BB=25, IBB=0, HBP=3, SO=90, GDP=8, SB=2, CS=1),
        dict(player_id="4", name="라", team="B", G=3, PA=0, AB=0, R=0, H=0, **{"2B": 0, "3B": 0},
             HR=0, TB=0, RBI=0, SAC=0, SF=0, BB=0, IBB=0, HBP=0, SO=0, GDP=0, SB=0, CS=0),
    ]).assign(TB=np.nan)


def team_batting(h):
    return pd.DataFrame([{"team": t, "G": 144, "PA": g.PA.sum(), "R": g.R.sum()} for t, g in h.groupby("team")])


def test_league_woba_equals_league_obp():
    h = hitters()
    ctx = stats.batting_context(h, team_batting(h), WEIGHTS)
    d = stats.batting(h, ctx)
    w = ctx["woba_weights"]
    num = (w["BB"] * (h.BB - h.IBB) + w["HBP"] * h.HBP + w["1B"] * (h.H - h["2B"] - h["3B"] - h.HR)
           + w["2B"] * h["2B"] + w["3B"] * h["3B"] + w["HR"] * h.HR).sum()
    den = (h.AB + h.BB - h.IBB + h.SF + h.HBP).sum()
    assert num / den == pytest.approx(ctx["lg_obp"])
    assert d.loc[d.player_id == "1", "wRC+"].item() > 100 > d.loc[d.player_id == "3", "wRC+"].item()


def test_league_average_wrc_plus_is_100_without_park():
    h = hitters()
    ctx = stats.batting_context(h, team_batting(h), WEIGHTS)
    d = stats.batting(h, ctx)
    avg = (d["wRC+"].fillna(0) * d.PA).sum() / d.PA.sum()
    assert avg == pytest.approx(100, abs=1.5)   # wOBA 분모가 PA와 조금 달라 정확히 100은 아니다


def test_hitter_park_lowers_wrc_plus():
    h = hitters()
    ctx = stats.batting_context(h, team_batting(h), WEIGHTS)
    neutral = stats.batting(h, ctx)
    hitter_park = stats.batting(h, ctx, {"A": 1.05})
    a = lambda d: d.loc[d.player_id == "1", "wRC+"].item()
    assert a(hitter_park) < a(neutral)
    assert a(neutral) - a(hitter_park) == pytest.approx(5.0, abs=1e-9)  # PF 0.05 → 정확히 5점


def test_classic_rates():
    h = hitters()
    ctx = stats.batting_context(h, team_batting(h), WEIGHTS)
    d = stats.batting(h, ctx).set_index("player_id")
    assert d.loc["1", "AVG"] == pytest.approx(170 / 500)
    assert d.loc["1", "OBP"] == pytest.approx((170 + 85 + 10) / (500 + 85 + 10 + 5))
    assert d.loc["1", "SLG"] == pytest.approx((98 + 70 + 6 + 140) / 500)
    assert np.isnan(d.loc["4", "AVG"]) and d.loc["4", "wRAA"] == 0


def test_qualified_uses_team_games():
    h = hitters()
    ctx = stats.batting_context(h, team_batting(h), WEIGHTS)
    d = stats.batting(h, ctx).set_index("player_id")
    assert d.loc["1", "qualified"]                 # 600 ≥ 144 × 3.1 = 446.4
    assert not d.loc["3", "qualified"]             # 400 < 446.4


def pitchers():
    return pd.DataFrame([
        dict(player_id="p1", name="갑", team="A", G=30, W=15, L=5, SV=0, HLD=0, IP=180.0, H=150, HR=12, BB=40,
             HBP=6, SO=190, R=60, ER=55, TBF=730, SAC=4, SF=4, QS=20),
        dict(player_id="p2", name="을", team="B", G=60, W=3, L=6, SV=10, HLD=12, IP=60 + 2 / 3, H=70, HR=9, BB=35,
             HBP=4, SO=50, R=40, ER=38, TBF=290, SAC=2, SF=3, QS=0),
    ])


def test_league_fip_equals_league_era():
    p = pitchers()
    ctx = stats.pitching_context(p)
    d = stats.pitching(p, ctx, {"A": 144, "B": 144})
    lg_fip = (d.FIP * d.IP).sum() / d.IP.sum()
    assert lg_fip == pytest.approx(ctx["lg_era"])
    assert d.loc[0, "ERA"] == pytest.approx(9 * 55 / 180)
    assert d.loc[0, "qualified"] and not d.loc[1, "qualified"]


def test_era_minus_with_pitcher_park():
    p = pitchers()
    ctx = stats.pitching_context(p)
    neutral = stats.pitching(p, ctx, {"A": 144, "B": 144})
    hitter_park = stats.pitching(p, ctx, {"A": 144, "B": 144}, {"A": 1.05})
    assert hitter_park.loc[0, "ERA-"] < neutral.loc[0, "ERA-"]   # 타자 친화 구장 투수는 보정 후 더 좋아진다


@pytest.mark.parametrize("text,expected", [("52 1/3", 52 + 1 / 3), ("2/3", 2 / 3), ("164", 164.0), ("-", np.nan)])
def test_parse_ip(text, expected):
    got = parse_ip(text)
    assert (np.isnan(got) and np.isnan(expected)) or got == pytest.approx(expected)


@pytest.mark.parametrize("ip,expected", [(52 + 1 / 3, "52 1/3"), (2 / 3, "2/3"), (164.0, "164"), (9.9999999, "10")])
def test_format_ip(ip, expected):
    assert stats.format_ip(ip) == expected


def test_to_number():
    assert to_number("1,234") == 1234 and np.isnan(to_number("-")) and to_number("0.337") == pytest.approx(0.337)
