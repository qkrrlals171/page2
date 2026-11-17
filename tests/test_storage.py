"""SQLite 저장·불러오기 테스트."""

import pandas as pd

from kbostats import storage


def _frames(tag):
    bat = pd.DataFrame([{"player_id": "1", "name": "가", "team": "A", "PA": 10, "wRC+": 120.0, "note": tag}])
    pit = pd.DataFrame([{"player_id": "9", "name": "갑", "team": "B", "IP": 5.0, "FIP": 3.5, "note": tag}])
    return bat, pit


def test_first_save_keeps_every_snapshot(tmp_path):
    db = tmp_path / "s.sqlite"
    bat, pit = _frames("a")
    storage.save(2026, "2026-05-01", bat, pit, {"batting": {}, "pitching": {}}, path=db)
    as_of, b, p, ctx = storage.latest(2026, path=db)
    assert as_of == "2026-05-01"
    assert b.loc[0, "player_id"] == "1" and p.loc[0, "player_id"] == "9"
    assert set(ctx) == {"batting", "pitching"}


def test_seasons_and_days_are_kept_separately(tmp_path):
    db = tmp_path / "s.sqlite"
    storage.save(2025, "2025-10-01", *_frames("old"), {"batting": {}, "pitching": {}}, path=db)
    storage.save(2026, "2026-05-01", *_frames("d1"), {"batting": {}, "pitching": {}}, path=db)
    storage.save(2026, "2026-05-02", *_frames("d2"), {"batting": {}, "pitching": {}}, path=db)
    assert storage.latest_season(path=db) == 2026
    assert storage.latest(2026, path=db)[1].loc[0, "note"] == "d2"
    assert storage.latest(2025, path=db)[1].loc[0, "note"] == "old"
