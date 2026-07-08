"""Integration test: a persisted snapshot DB makes regime confirmation and the
day-over-day diff actually work across runs.

This is the exact behavior GitHub Pages needs — the CI container is ephemeral,
so unless the DB survives between runs, every day looks like "首次记录" and the
2-day confirmation / 「较昨日变化」 never fire. These assertions fail if the
wiring (runner → snapshot.load_previous/load_recent) regresses.
"""
from atlas import runner, snapshot


def test_snapshots_accumulate_and_enable_confirmation(tmp_path):
    db = str(tmp_path / "snap.sqlite")
    out = str(tmp_path / "dash.html")

    # Day 1: cold DB → a "first record", no previous regime.
    r1 = runner.run(offline=True, db_path=db, output=out, as_of="2026-07-06")
    assert r1.market_regime.prev_regime is None
    assert "首次记录" in r1.market_regime.reason

    # Day 2: same DB → the run must SEE day 1 as its previous snapshot.
    r2 = runner.run(offline=True, db_path=db, output=out, as_of="2026-07-07")

    # Both days persisted.
    assert snapshot.load_report("2026-07-06", db) is not None
    assert snapshot.load_report("2026-07-07", db) is not None

    # Day 2 loaded day 1 as its predecessor (the wiring Pages depends on).
    prev = snapshot.load_previous("2026-07-07", db)
    assert prev is not None and prev.date == "2026-07-06"

    # Confirmation engaged: day 2 has a previous confirmed regime (not a cold start).
    assert r2.market_regime.prev_regime is not None
    assert "首次记录" not in r2.market_regime.reason


def test_run_reuses_prior_db_file(tmp_path):
    """A second process pointed at the same file keeps accumulating rows."""
    db = str(tmp_path / "snap.sqlite")
    out = str(tmp_path / "dash.html")
    runner.run(offline=True, db_path=db, output=out, as_of="2026-07-01")
    runner.run(offline=True, db_path=db, output=out, as_of="2026-07-02")
    runner.run(offline=True, db_path=db, output=out, as_of="2026-07-03")
    recent = snapshot.load_recent("2026-07-04", 10, db)
    assert [r.date for r in recent] == ["2026-07-03", "2026-07-02", "2026-07-01"]
