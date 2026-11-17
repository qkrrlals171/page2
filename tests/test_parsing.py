"""KBO 페이지 파싱 테스트 (실제 페이지 구조를 줄인 HTML 조각 사용)."""

import pandas as pd

from kbostats import parks
from kbostats.kbo_client import parse_form, parse_tables

PAGE = """
<form method="post" action="./Basic1.aspx" id="mainForm">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="abc&amp;def" />
<input type="hidden" name="ctl00$x$hfPage" id="hfPage" value="1" />
<select name="ctl00$x$ddlSeason$ddlSeason" id="s">
  <option value="2025">2025</option><option selected="selected" value="2026">2026</option></select>
<select name="ctl00$x$ddlTeam$ddlTeam" id="t">
  <option selected="selected" value="">팀 선택</option><option value="HT">KIA</option></select>
<table class="tData01"><thead><tr><th>순위</th><th>선수명</th><th>팀명</th><th>AVG</th></tr></thead>
<tbody>
<tr><td>1</td><td><a href="/Record/Player/HitterDetail/Basic.aspx?playerId=76232">양의지</a></td><td>두산</td><td>0.337</td></tr>
<tr><td>2</td><td><a href="/Record/Player/HitterDetail/Basic.aspx?playerId=52001">안현민</a></td><td>KT</td><td>0.334</td></tr>
</tbody></table>
<a href="javascript:__doPostBack(&#39;ctl00$x$ucPager$btnNo2&#39;,&#39;&#39;)">2</a>
</form>
"""


def test_parse_tables_with_player_ids():
    t = parse_tables(PAGE)[0]
    assert t.header == ["순위", "선수명", "팀명", "AVG"]
    assert t.rows[0] == ["1", "양의지", "두산", "0.337"]
    assert t.player_ids == ["76232", "52001"]


def test_parse_form():
    f = parse_form(PAGE)
    assert f.fields["__VIEWSTATE"] == "abc&def"
    assert f.fields[f.name("ddlSeason")] == "2026"
    assert f.fields[f.name("ddlTeam")] == ""
    assert ("HT", "KIA") in f.options[f.name("ddlTeam")]
    assert any(t.endswith("ucPager$btnNo2") for t in f.postback_targets)


def _games(rows):
    return pd.DataFrame(rows, columns=["season", "home", "away", "park", "home_score", "away_score"]).assign(finished=True)


def test_park_factors_detect_hitter_park_and_shared_park():
    rows = []
    # 팀 X(구장 P, 득점 많음), 팀 Y·Z는 구장 Q를 같이 씀
    for _ in range(20):
        rows += [(2025, "X", "Y", "P", 7, 7), (2025, "X", "Z", "P", 7, 7),
                 (2025, "Y", "X", "Q", 4, 4), (2025, "Z", "X", "Q", 4, 4),
                 (2025, "Y", "Z", "Q", 4, 4), (2025, "Z", "Y", "Q", 4, 4)]
    rows += [(2025, "X", "Y", "제2구장", 9, 9)]          # 제2구장 경기는 무시되어야 함
    r = parks.compute(_games(rows), regression_games=0)
    assert set(r["parks"]) == {"P", "Q"}
    assert r["parks"]["P"]["pf"] > 1 > r["parks"]["Q"]["pf"]
    assert r["parks"]["Q"]["teams"] == ["Y", "Z"]
    assert r["team_park"] == {"X": "P", "Y": "Q", "Z": "Q"}


def test_regression_pulls_toward_one():
    rows = [(2025, "X", "Y", "P", 7, 7), (2025, "Y", "X", "Q", 4, 4)] * 10
    loose = parks.compute(_games(rows), regression_games=0)["parks"]["P"]
    tight = parks.compute(_games(rows), regression_games=100)["parks"]["P"]
    assert 1 < tight["pf_regressed"] < loose["pf_regressed"]
    assert tight["pf_half"] == round((1 + tight["pf_regressed"]) / 2, 4)
