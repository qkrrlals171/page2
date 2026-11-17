"""경로, 수집 설정, 리그 규칙 상수를 한곳에 모아 둔다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # 수집 원본(CSV) 보관: data/raw/<시즌>/<날짜>/*.csv
PBP_DIR = DATA_DIR / "pbp"            # 가중치 계산용 플레이별 데이터(parquet)
WEIGHTS_PATH = DATA_DIR / "weights.json"
PARKS_PATH = DATA_DIR / "parks.json"
DB_PATH = DATA_DIR / "kbostats.sqlite"
SITE_DIR = ROOT / "site"
TEMPLATE_PATH = ROOT / "templates" / "page.html"

# --- 수집 ---------------------------------------------------------------
KBO_BASE = "https://www.koreabaseball.com"
# 브라우저로 위장하지 않고, 어떤 프로그램인지 그대로 밝힌다.
USER_AGENT = "kbo-stats-personal/0.1 (personal, non-commercial statistics)"
REQUEST_DELAY = 0.8      # 요청 사이 간격(초). 사이트에 부담을 주지 않도록 줄이지 말 것.
MAX_RETRIES = 6          # 연결이 가끔 끊기므로 재시도한다.
TIMEOUT = 30

REGULAR_SEASON = "0"     # 기록실 시리즈 코드: 0 = 정규시즌
SCHEDULE_SERIES = "0,9,6"  # 일정 API의 정규시즌 시리즈 목록

# 공개 플레이별 데이터셋(CC BY 4.0). 가중치(RE24 → 선형 가중치) 계산에만 쓴다.
PBP_URL = ("https://huggingface.co/datasets/slothman3878/kbo_playbyplay/"
           "resolve/main/v0/kbo_pbp_{season}.parquet")

# --- 리그 규칙 -------------------------------------------------------------
QUALIFY_PA_PER_GAME = 3.1    # 규정타석 = 팀 경기수 × 3.1
QUALIFY_IP_PER_GAME = 1.0    # 규정이닝 = 팀 경기수 × 1.0

# --- 파크팩터 ------------------------------------------------------------
# 회귀 강도: 홈 경기 수 n에 대해 pf = 1 + (raw - 1) * n / (n + PARK_REGRESSION_GAMES)
# 100경기면 1시즌(72경기)은 약 42%, 3시즌(216경기)은 약 68%만 반영된다. 대략적인 값이므로 조정 가능.
PARK_REGRESSION_GAMES = 100
