"""KBO 공식 기록실(koreabaseball.com)과 통신하는 저수준 클라이언트.

기록실 페이지는 ASP.NET WebForms라서 시즌·시리즈·팀을 바꿀 때마다
폼 전체(__VIEWSTATE 등)를 다시 POST(포스트백)해야 한다.

확인된 주의점
- 시즌과 시리즈를 한 번에 바꾸면 일부 페이지(타자 Basic2)가 오류 페이지를 돌려준다.
  → 시리즈를 먼저, 그다음 시즌을 각각 따로 포스트백한다.
- 팀을 고르지 않으면 규정타석/규정이닝 선수만 나온다.
  → 팀별로 나눠 받으면 0타석 선수까지 전부 나온다.
- 이적 선수는 현재 소속팀 아래에 시즌 전체 기록으로 한 번만 나온다.
- 연결이 가끔 끊기므로 재시도가 필수다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser

import requests

from . import config


class KboError(RuntimeError):
    pass


# --- HTML 파싱 ------------------------------------------------------------

@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    player_ids: list[str | None]


class _TableParser(HTMLParser):
    """페이지의 모든 <table>에서 셀 텍스트와 행별 playerId 링크를 뽑는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[tuple[list[str], str | None]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._row_pid: str | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 1 and tag == "tr":
            self._row, self._row_pid = [], None
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif self._depth == 1 and tag == "a" and self._row is not None:
            m = re.search(r"playerId=(\d+)", dict(attrs).get("href") or "")
            if m and self._row_pid is None:
                self._row_pid = m.group(1)

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append((self._row, self._row_pid))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html: str) -> list[Table]:
    p = _TableParser()
    p.feed(html)
    out = []
    for t in p.tables:
        if not t:
            continue
        header, _ = t[0]
        body = t[1:]
        out.append(Table(header, [r for r, _ in body], [pid for _, pid in body]))
    return out


@dataclass
class Form:
    fields: dict[str, str] = field(default_factory=dict)       # 전송할 값
    options: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # select 이름 → (값, 라벨)
    postback_targets: list[str] = field(default_factory=list)  # __doPostBack 대상(페이저 등)

    def name(self, suffix: str) -> str:
        """'...$ddlSeries$ddlSeries' 같은 긴 컨트롤 이름을 끝부분으로 찾는다."""
        hits = [n for n in self.fields if n.endswith(suffix)]
        if not hits:
            raise KboError(f"폼에서 '{suffix}' 컨트롤을 찾지 못했습니다")
        return hits[0]


def parse_form(html: str) -> Form:
    f = Form()
    for tag in re.findall(r"<input[^>]+>", html):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if attrs.get("type") == "hidden" and "name" in attrs:
            f.fields[attrs["name"]] = _unescape(attrs.get("value", ""))
    for name, block in re.findall(r'<select name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        opts = re.findall(r'<option( selected="selected")? value="([^"]*)">([^<]*)', block)
        f.options[name] = [(v, label.strip()) for _, v, label in opts]
        selected = [v for sel, v, _ in opts if sel]
        f.fields[name] = selected[0] if selected else (opts[0][1] if opts else "")
    f.postback_targets = re.findall(r"__doPostBack\((?:&#39;|')([^'&]+)(?:&#39;|')", html)
    return f


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
             .replace("&lt;", "<").replace("&gt;", ">"))


def page_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return " ".join(m.group(1).split()) if m else ""


# --- HTTP -----------------------------------------------------------------

class KboClient:
    def __init__(self, delay: float = config.REQUEST_DELAY, retries: int = config.MAX_RETRIES,
                 verbose: bool = False) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self.delay = delay
        self.retries = retries
        self.verbose = verbose
        self.request_count = 0

    def request(self, method: str, url: str, **kw) -> str:
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            time.sleep(self.delay)
            try:
                r = self.session.request(method, url, timeout=config.TIMEOUT, **kw)
                self.request_count += 1
                r.raise_for_status()
                text = r.content.decode("utf-8-sig", errors="replace")
                if "에러" in page_title(text):
                    raise KboError(f"KBO 오류 페이지: {url}")
                return text
            except (requests.RequestException, KboError) as e:
                last = e
                if self.verbose:
                    print(f"  재시도 {attempt}/{self.retries}: {type(e).__name__}")
                time.sleep(min(2 * attempt, 10))
        raise KboError(f"요청 실패: {url} ({last})")

    def get(self, url: str) -> str:
        return self.request("GET", url)

    def postback(self, url: str, html: str, target_suffix: str | None = None,
                 target: str | None = None, **values: str) -> str:
        """현재 페이지 상태(html)에서 컨트롤 하나를 바꿔 포스트백한다."""
        form = parse_form(html)
        data = dict(form.fields)
        for suffix, value in values.items():
            data[form.name(suffix)] = value
        data["__EVENTTARGET"] = target or form.name(target_suffix)
        data["__EVENTARGUMENT"] = ""
        out = self.request("POST", url, data=data)
        # 조용히 실패하는 경우(값이 반영되지 않은 페이지)를 잡아낸다.
        new = parse_form(out)
        for suffix, value in values.items():
            if new.fields.get(new.name(suffix)) != value:
                raise KboError(f"{suffix}={value} 가 반영되지 않았습니다: {url}")
        return out

    def ajax_post(self, url: str, data: dict, referer: str) -> dict:
        text = self.request("POST", url, data=data,
                            headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"})
        return json.loads(text)


class RecordView:
    """기록실 표 한 종류(예: 타자 기본기록 1)를 시즌·팀별로 읽는다."""

    def __init__(self, client: KboClient, path: str) -> None:
        self.client = client
        self.url = config.KBO_BASE + path
        self.base_html: str | None = None

    def load(self, season: int, series: str = config.REGULAR_SEASON) -> "RecordView":
        c = self.client

        def current(html: str, suffix: str) -> str:
            form = parse_form(html)
            return form.fields.get(form.name(suffix), "")

        html = c.get(self.url)
        # 순서가 중요하다: 시리즈 → 시즌 (한 번에 바꾸면 일부 페이지가 오류를 낸다)
        if current(html, "ddlSeries") != series:
            html = c.postback(self.url, html, "ddlSeries", ddlSeries=series)
        if current(html, "ddlSeason") != str(season):
            html = c.postback(self.url, html, "ddlSeason", ddlSeason=str(season))
        self.base_html = html
        return self

    def teams(self) -> list[tuple[str, str]]:
        form = parse_form(self.base_html)
        return [(v, label) for v, label in form.options.get(form.name("ddlTeam"), []) if v]

    def table(self, team: str | None = None) -> Table:
        """팀 하나(또는 전체)의 모든 페이지를 이어 붙인 표."""
        html = self.base_html
        if team:
            html = self.client.postback(self.url, html, "ddlTeam", ddlTeam=team)
        tables = parse_tables(html)
        if not tables:
            raise KboError(f"표가 없습니다: {self.url}")
        header, rows, pids = tables[0].header, list(tables[0].rows), list(tables[0].player_ids)
        seen = {1}
        while True:
            pages = sorted({int(m.group(1)) for t in parse_form(html).postback_targets
                            if (m := re.search(r"ucPager\$btnNo(\d+)$", t))})
            nxt = [p for p in pages if p not in seen]
            if not nxt:
                break
            page = nxt[0]
            target = next(t for t in parse_form(html).postback_targets if t.endswith(f"btnNo{page}"))
            html = self.client.postback(self.url, html, target=target)
            seen.add(page)
            t = parse_tables(html)[0]
            rows += t.rows
            pids += t.player_ids
        if rows and len(rows) == 1 and len(rows[0]) == 1:   # "기록이 없습니다"
            rows, pids = [], []
        return Table(header, rows, pids)
