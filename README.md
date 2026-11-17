# kbo-stats

KBO 정규시즌 **클래식 스탯 + 세이버 스탯** 페이지를 만드는 프로그램입니다.
매일 경기가 끝난 뒤 실행하면 KBO 공식 기록실에서 시즌 누적 기록을 받아 계산하고,
정렬·필터가 되는 HTML 페이지 한 장(`site/index.html`)을 만듭니다.

| 구분 | 타자 | 투수 |
|---|---|---|
| 클래식 | G, PA, AB, R, H, 2B, 3B, HR, RBI, SB, CS, BB, IBB, HBP, SO, GDP, AVG, OBP, SLG, OPS | G, W, L, SV, HLD, QS, IP, H, HR, BB, HBP, SO, R, ER, ERA, WHIP |
| 세이버 | ISO, BABIP, BB%, K%, BB/K, wOBA, wRAA, wRC, **wRC+**, OPS+ | K/9, BB/9, HR/9, K%, BB%, K-BB%, BABIP, LOB%, **FIP**, ERA-, FIP- |

FanGraphs나 Baseball-Reference 값을 가져오지 않습니다. 가중치, 파크팩터, 리그 상수는 모두 직접 계산합니다.

**페이지 주소:** https://qkrrlals171.github.io/page2/

## GitHub에서 자동으로 돌아가는 방식

`.github/workflows/update.yml`이 정규시즌 기간(3~10월)에 매일 두 번 실행됩니다.
실행 시각은 23:40 KST(그날 경기 반영)와 09:10 KST(밤사이 기록 정정 반영)입니다.
GitHub 서버가 다음 순서로 처리하므로 PC를 켜 둘 필요가 없습니다.

1. 테스트 실행
2. KBO 기록실에서 그날까지의 시즌 기록 받기
3. 스탯 계산과 페이지 생성
4. GitHub Pages에 배포

- **수동 실행:** 저장소의 **Actions** 탭 → **매일 스탯 갱신** → **Run workflow**. 시즌 칸에 `2025`처럼 적으면 그 시즌으로 만듭니다.
- **실패 알림:** 실행이 실패하면 GitHub가 저장소 주인에게 이메일로 알려 줍니다.
- **커밋 없음:** 매일 실행해도 저장소에 커밋이 쌓이지 않습니다. 페이지만 새로 배포됩니다.
- **가중치·파크팩터 갱신:** `data/weights.json`, `data/parks.json`은 시즌이 끝난 뒤 PC에서 `build-weights` / `build-parks`로 다시 만들어 커밋하면 됩니다.

## 구조

```
kbostats/
  config.py      경로, 요청 간격, 리그 규칙(규정타석 3.1 등), 파크팩터 회귀 강도
  kbo_client.py  KBO 기록실 통신 (ASP.NET 포스트백, 페이지 넘김, 재시도)
  collect.py     타자·투수·팀 시즌 기록, 경기 일정·결과(구장 포함) 수집
  weights.py     플레이별 데이터 → RE24 → 결과별 득점 가치 (wOBA 가중치의 바탕)
  parks.py       경기 결과 → 구장별 득점 파크팩터 (원정 구장 보정, 회귀, 절반 적용)
  stats.py       클래식·세이버 스탯 계산 (순수 함수, 네트워크 없음)
  storage.py     SQLite 저장 (시즌별 최신 결과 + 날짜별 스냅샷)
  render.py      정적 HTML 페이지 생성 (열 정의는 여기서 수정)
  cli.py         명령줄
templates/page.html  페이지 디자인과 정렬·필터 스크립트
tests/               공식 성질 테스트 (리그 wOBA = 리그 출루율, 리그 FIP = 리그 ERA 등)
data/                weights.json, parks.json, 수집 원본(raw/), DB
site/index.html      결과 페이지
```

## 설치

Python 3.10 이상.

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

## 사용

처음 한 번(그리고 시즌이 끝날 때마다) 가중치와 파크팩터를 만듭니다.

```bash
python -m kbostats build-weights --seasons 2023 2024 2025
```

```bash
python -m kbostats build-parks --seasons 2023 2024 2025 2026
```

매일 실행하는 명령입니다. 약 100번 요청하고 2분쯤 걸립니다.

```bash
python -m kbostats update
```

| 명령 | 하는 일 |
|---|---|
| `update --season 2025` | 지난 시즌 계산 |
| `update --offline` | 새로 받지 않고 가장 최근 원본으로 다시 계산 (공식이나 페이지만 고쳤을 때) |
| `render` | DB에 저장된 최신 결과로 페이지만 다시 생성 |
| `-v` | 재시도 로그 표시 |

`update`는 끝나면 공식 기록과 맞춰 봅니다. 출루율·평균자책점이 공식 값과 다르거나,
선수 합계가 팀 합계와 맞지 않으면 `확인 필요:` 로 알려 줍니다.

### 매일 자동 실행 (cron 예시)

경기가 대개 22시 전후에 끝나므로 23:40에 한 번, 다음 날 아침 기록 정정을 반영하려고 9:10에 한 번 더 실행합니다.

```bash
crontab -e
```

```
40 23 * * * cd /path/to/kbo-stats && .venv/bin/python -m kbostats update >> data/update.log 2>&1
10 9 * * * cd /path/to/kbo-stats && .venv/bin/python -m kbostats update >> data/update.log 2>&1
```

Windows는 작업 스케줄러에 같은 명령을 등록하면 됩니다. `site/` 폴더를 GitHub Pages 같은 정적 호스팅에 올리면 웹페이지로 볼 수 있습니다.

## 계산 방법

**wOBA 가중치.** 2023~2025 플레이별 데이터로 24개 상황(아웃 × 주자)의 득점 기대값을 구합니다.
그다음 결과별로 `RE(타석 후) − RE(타석 전) + 득점`의 평균을 냅니다.
아웃의 가치를 0으로 옮긴 값에, 그 시즌 리그 wOBA가 리그 출루율과 같아지도록 스케일을 곱합니다.

**wRC+.**

```
wRAA = (wOBA − 리그 wOBA) / 스케일 × PA
wRC+ = ((wRAA/PA + 리그 R/PA) + (리그 R/PA − PF × 리그 R/PA)) / 리그 R/PA × 100
```

**파크팩터.** 같은 구장을 쓰는 팀들의 홈 경기당 득점(양 팀 합)과 원정 경기당 득점을 비교합니다.
원정 구장 보정은 반복 계산으로 하고, 홈 경기 수에 따라 1 쪽으로 회귀합니다(`config.PARK_REGRESSION_GAMES`).
선수에게는 절반 적용 값 `(1 + PF) / 2`를 씁니다. 잠실은 LG·두산을 합쳐 하나의 구장으로 보고,
제2구장(포항·울산·청주) 경기는 뺍니다. 일정 페이지가 구장명을 '한밭'(~2024)과 '대전'(2025~)으로 따로 표기하므로
한화 새 구장은 자동으로 별도 구장이 됩니다.

**FIP.** `(13·HR + 3·(BB+HBP) − 2·SO) / IP + 상수`. 상수는 리그 FIP가 리그 ERA와 같아지도록 매일 다시 계산합니다.

## 알려진 한계

- **고의4구.** 가중치용 플레이별 데이터에 고의4구 구분이 없어 볼넷 가중치에 섞여 있습니다. 영향은 작습니다.
  선수 wOBA 계산에서는 공식 기록의 IBB를 빼므로 정확합니다.
- **이적 선수.** 기록실이 현재 소속팀 아래에 시즌 전체 기록으로 보여 주므로, 구장 보정도 현재 팀 구장 하나로 합니다.
- **가중치 연도.** 그해 플레이별 데이터 대신 직전 시즌 데이터로 만든 가중치를 씁니다. 결과별 가치는 해마다 거의 같아서 차이는 작습니다.
- **WAR는 없습니다.** 수비와 주루 점수에는 플레이별 기록이나 추적 데이터가 필요합니다.

## 데이터 출처와 이용

- **선수·팀 기록, 경기 일정과 결과.** [KBO 공식 기록실](https://www.koreabaseball.com).
  개인·비상업 용도로 하루 1~2회, 요청 사이 0.8초 간격으로 받습니다. 프로그램 이름을 User-Agent에 그대로 밝힙니다.
  KBO 홈페이지 이용 조건은 직접 확인하세요. 받은 원본을 재배포하거나 상업적으로 쓰지 마세요.
- **가중치 계산용 플레이별 데이터.** [slothman3878/kbo_playbyplay](https://huggingface.co/datasets/slothman3878/kbo_playbyplay) (CC BY 4.0).
  1년에 한 번 내려받습니다.
- 네이버 스포츠 API는 쓰지 않습니다(비공식 API이고, 네이버 약관이 자동화 수집을 제한합니다).

## 테스트

```bash
python -m pytest -q
```

## 확장 아이디어

- 날짜별 스냅샷(`snapshots` 테이블)으로 선수 wRC+ 추이 그래프 그리기
- 팀 탭 추가 (팀 wRC+, 팀 FIP)
- 상황별 기록(기록실 `ddlSituation`: 월별, 홈/원정, 좌우)으로 스플릿 페이지 만들기
- 2군(퓨처스) 기록
