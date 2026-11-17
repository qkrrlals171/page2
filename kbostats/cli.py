"""명령줄 진입점: python -m kbostats <명령>

  build-weights  플레이별 데이터로 wOBA 가중치를 만든다 (1년에 한 번)
  build-parks    경기 결과로 파크팩터를 만든다 (시즌 전, 또는 가끔)
  update         오늘까지의 시즌 기록을 받아 계산·저장하고 페이지를 만든다 (매일)
  render         저장된 최신 결과로 페이지만 다시 만든다
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import collect, config, parks, render, stats, storage, weights
from .kbo_client import KboClient, KboError


def today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


# --- build-weights ----------------------------------------------------------

def cmd_build_weights(args) -> None:
    r = weights.build(args.seasons)
    print(f"\n{r['plate_appearances']:,}타석으로 계산 → {config.WEIGHTS_PATH}")
    print("결과별 득점 가치:", r["run_values"])
    print("아웃 대비 가중치:", r["weights_above_out"])


# --- build-parks ------------------------------------------------------------

def load_schedule(client: KboClient, season: int) -> pd.DataFrame:
    cache = config.RAW_DIR / f"schedule_{season}.csv"
    if cache.exists() and season < today().year:     # 끝난 시즌은 다시 받지 않는다
        return pd.read_csv(cache)
    print(f"{season} 경기 일정·결과 받는 중…")
    df = collect.fetch_schedule(client, season)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def cmd_build_parks(args) -> None:
    client = KboClient(verbose=args.verbose)
    games = pd.concat([load_schedule(client, s) for s in args.seasons], ignore_index=True)
    r = parks.build(games, regression_games=args.regression)
    print(f"\n파크팩터 ({', '.join(map(str, r['seasons']))}) → {config.PARKS_PATH}")
    print(f"{'구장':<6}{'팀':<12}{'홈경기':>6}{'원값':>8}{'회귀후':>8}{'wRC+용':>8}")
    for k, v in sorted(r["parks"].items(), key=lambda kv: -kv[1]["pf_regressed"]):
        print(f"{k:<6}{','.join(v['teams']):<12}{v['games']:>6}{v['pf']:>8.3f}{v['pf_regressed']:>8.3f}{v['pf_half']:>8.3f}")


# --- update -----------------------------------------------------------------

RAW_FILES = ("hitters", "pitchers", "team_batting", "team_pitching")


def has_records(team_batting: pd.DataFrame) -> bool:
    return not team_batting.empty and "PA" in team_batting and team_batting["PA"].sum() > 0


def fetch_raw(season: int | None, as_of: str, verbose: bool) -> tuple[int, dict[str, pd.DataFrame]]:
    """season이 없으면 올해, 올해 정규시즌 기록이 아직 없으면(개막 전) 작년을 받는다."""
    client = KboClient(verbose=verbose)
    candidates = [season] if season else [today().year, today().year - 1]
    for s in candidates:
        try:
            team_batting = collect.fetch_team_batting(client, s)
        except KboError as e:          # 기록실에 아직 그 시즌 선택지가 없는 경우 등
            print(f"{s} 시즌을 열 수 없습니다: {e}")
            continue
        if has_records(team_batting):
            season = s
            break
        print(f"{s} 정규시즌 기록이 아직 없습니다.")
    else:
        raise SystemExit("받을 정규시즌 기록이 없습니다.")
    print(f"{season} 시즌 기록 받는 중 (KBO 기록실)…")
    raw = {
        "hitters": collect.fetch_hitters(client, season),
        "pitchers": collect.fetch_pitchers(client, season),
        "team_batting": team_batting,
        "team_pitching": collect.fetch_team_pitching(client, season),
    }
    out = config.RAW_DIR / str(season) / as_of
    out.mkdir(parents=True, exist_ok=True)
    for name, df in raw.items():
        df.to_csv(out / f"{name}.csv", index=False)
    print(f"요청 {client.request_count}회, 원본 저장 → {out}")
    return season, raw


def latest_raw_season() -> int:
    seasons = [int(p.name) for p in config.RAW_DIR.glob("*") if p.is_dir() and p.name.isdigit()]
    if not seasons:
        raise SystemExit(f"{config.RAW_DIR} 에 저장된 원본이 없습니다. --offline 없이 실행하세요.")
    return max(seasons)


def load_raw(season: int) -> tuple[str, dict[str, pd.DataFrame]]:
    base = config.RAW_DIR / str(season)
    dirs = sorted(p for p in base.glob("*") if p.is_dir()) if base.exists() else []
    if not dirs:
        raise SystemExit(f"{base} 에 저장된 원본이 없습니다. --offline 없이 실행하세요.")
    d = dirs[-1]
    return d.name, {n: pd.read_csv(d / f"{n}.csv", dtype={"player_id": str}) for n in RAW_FILES}


def compute(raw: dict[str, pd.DataFrame]):
    w = weights.load()
    p = parks.load()
    pf = parks.team_factors(p)
    bctx = stats.batting_context(raw["hitters"], raw["team_batting"], w)
    pctx = stats.pitching_context(raw["pitchers"])
    bat = stats.batting(raw["hitters"], bctx, pf)
    pit = stats.pitching(raw["pitchers"], pctx, bctx["team_games"], pf)
    return bat, pit, {"batting": bctx, "pitching": pctx}, w, p


def check(raw, bat, pit) -> list[str]:
    """공식 기록과 맞춰 보고 이상한 점을 알린다."""
    msgs = []
    tb, tp = raw["team_batting"], raw["team_pitching"]
    for col in ("PA", "H", "HR", "R"):
        a, b = raw["hitters"][col].sum(), tb[col].sum()
        if abs(a - b) > 0:
            msgs.append(f"타자 {col} 합계 {a:.0f} ≠ 팀 합계 {b:.0f}")
    # 자책점(ER)은 비교하지 않는다: 이닝 중 투수 교체 시 투수별 자책점을 따로 판정하므로
    # 팀 자책점이 투수 자책점 합계보다 작은 것이 정상이다.
    for col in ("IP", "SO", "HR"):
        a, b = raw["pitchers"][col].sum(), tp[col].sum()
        if abs(a - b) > 0.01:
            msgs.append(f"투수 {col} 합계 {a:.1f} ≠ 팀 합계 {b:.1f}")
    if "off_OBP" in bat:
        diff = (bat["OBP"] - bat["off_OBP"]).abs()
        n = int((diff > 0.0015).sum())
        if n:
            msgs.append(f"출루율이 공식 기록과 다른 타자 {n}명")
    if "off_ERA" in pit:
        diff = (pit["ERA"] - pit["off_ERA"]).abs()
        n = int((diff > 0.006).sum())
        if n:
            msgs.append(f"평균자책점이 공식 기록과 다른 투수 {n}명")
    return msgs


def summarize(bat, pit, ctx) -> None:
    b = ctx["batting"]
    q = bat[bat["qualified"]]
    pa_weighted = (bat["wRC+"].fillna(0) * bat["PA"]).sum() / bat["PA"].sum()
    print(f"\n리그 wOBA {b['lg_woba']:.3f} · 스케일 {b['woba_scale']:.3f} · R/PA {b['lg_r_pa']:.4f}"
          f" · 타석 가중 평균 wRC+ {pa_weighted:.1f}")
    print(f"리그 ERA {ctx['pitching']['lg_era']:.2f} · FIP 상수 {ctx['pitching']['fip_constant']:.3f}")
    print(f"\n규정타석 {len(q)}명 중 wRC+ 상위 5")
    for _, r in q.sort_values("wRC+", ascending=False).head(5).iterrows():
        print(f"  {r['name']:<6} {r['team']:<4} PA {r['PA']:>4.0f}  wOBA {r['wOBA']:.3f}  wRC+ {r['wRC+']:.0f}")


def cmd_update(args) -> None:
    if args.offline:
        season = args.season or latest_raw_season()
        as_of, raw = load_raw(season)
    else:
        as_of = today().isoformat()
        season, raw = fetch_raw(args.season, as_of, args.verbose)
    bat, pit, ctx, w, p = compute(raw)
    for m in check(raw, bat, pit):
        print("확인 필요:", m)
    storage.save(season, as_of, bat, pit, ctx)
    out = render.render(season, as_of, bat, pit, ctx, w, p, Path(args.out))
    summarize(bat, pit, ctx)
    print(f"\n페이지 → {out}")


def cmd_render(args) -> None:
    season = args.season or storage.latest_season()
    as_of, bat, pit, ctx = storage.latest(season)
    out = render.render(season, as_of, bat, pit, ctx, weights.load(), parks.load(), Path(args.out))
    print(f"페이지 → {out}")


def main(argv=None) -> None:
    year = today().year
    ap = argparse.ArgumentParser(prog="kbostats", description="KBO 클래식·세이버 스탯 페이지 생성기")
    ap.add_argument("-v", "--verbose", action="store_true", help="재시도 등 자세한 로그")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("build-weights", help="플레이별 데이터로 wOBA 가중치 만들기")
    s.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    s.set_defaults(func=cmd_build_weights)

    s = sub.add_parser("build-parks", help="경기 결과로 파크팩터 만들기")
    s.add_argument("--seasons", type=int, nargs="+", default=[year - 3, year - 2, year - 1, year])
    s.add_argument("--regression", type=float, default=config.PARK_REGRESSION_GAMES,
                   help="회귀 강도(경기 수). 클수록 1에 가깝게 당긴다")
    s.set_defaults(func=cmd_build_parks)

    s = sub.add_parser("update", help="시즌 기록 받아서 계산·저장·페이지 생성")
    s.add_argument("--season", type=int, help="생략하면 올해 (개막 전이면 작년)")
    s.add_argument("--offline", action="store_true", help="새로 받지 않고 가장 최근 원본으로 다시 계산")
    s.add_argument("--out", default=str(config.SITE_DIR / "index.html"))
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("render", help="저장된 결과로 페이지만 다시 만들기")
    s.add_argument("--season", type=int)
    s.add_argument("--out", default=str(config.SITE_DIR / "index.html"))
    s.set_defaults(func=cmd_render)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
