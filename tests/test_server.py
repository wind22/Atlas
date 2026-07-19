from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from atlas import server
from atlas.server import AtlasRuntime, next_scheduled_run, restore_state_archive


NY = ZoneInfo("America/New_York")


def test_next_run_same_weekday_before_close() -> None:
    now = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)  # Mon 15:00 ET
    nxt = next_scheduled_run(now)
    assert nxt == datetime(2026, 7, 13, 16, 30, tzinfo=NY)


def test_next_run_skips_weekend_and_respects_dst() -> None:
    now = datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc)  # Fri 18:00 ET
    nxt = next_scheduled_run(now)
    assert nxt == datetime(2026, 7, 13, 16, 30, tzinfo=NY)
    assert nxt.astimezone(timezone.utc).hour == 20


def test_next_run_requires_timezone() -> None:
    with pytest.raises(ValueError):
        next_scheduled_run(datetime(2026, 7, 13, 12, 0))


def _archive_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Atlas-data/atlas_snapshots.sqlite", b"sqlite-seed")
        archive.writestr("Atlas-data/site-data/latest.json", b'{"ok": true}')
        archive.writestr("Atlas-data/site-data/daily/2026-07-10.json", b"{}")
        archive.writestr("Atlas-data/other.txt", b"ignored")
    return buffer.getvalue()


def test_restore_state_archive(tmp_path: Path) -> None:
    db_path = tmp_path / "atlas.sqlite"
    data_dir = tmp_path / "public" / "data"

    assert restore_state_archive(
        _archive_bytes(), db_path=db_path, public_data_dir=data_dir
    )
    assert db_path.read_bytes() == b"sqlite-seed"
    assert (data_dir / "latest.json").read_text() == '{"ok": true}'
    assert (data_dir / "daily" / "2026-07-10.json").read_text() == "{}"
    assert not (tmp_path / "other.txt").exists()


def test_restore_never_overwrites_existing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "atlas.sqlite"
    db_path.write_bytes(b"current")

    assert not restore_state_archive(
        _archive_bytes(), db_path=db_path, public_data_dir=tmp_path / "data"
    )
    assert db_path.read_bytes() == b"current"


def test_restore_rejects_archive_without_database(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Atlas-data/site-data/latest.json", b"{}")

    assert not restore_state_archive(
        buffer.getvalue(),
        db_path=tmp_path / "atlas.sqlite",
        public_data_dir=tmp_path / "data",
    )
    assert not (tmp_path / "atlas.sqlite").exists()


def test_bootstrap_empty_volume_falls_back_to_image_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    public_dir = state_dir / "public"
    seed_dir = tmp_path / "seed" / "public"
    seed_dir.mkdir(parents=True)
    (seed_dir / "index.html").write_text("seed site", encoding="utf-8")
    (seed_dir.parent / "atlas_snapshots.sqlite").write_bytes(b"seed db")
    monkeypatch.setenv("ATLAS_STATE_ARCHIVE_URL", "")

    server._bootstrap_state(state_dir, public_dir, seed_dir)

    assert (public_dir / "index.html").read_text(encoding="utf-8") == "seed site"
    assert (state_dir / "atlas_snapshots.sqlite").read_bytes() == b"seed db"


def test_bootstrap_recovers_interrupted_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    previous_dir = state_dir / ".public-previous"
    previous_dir.mkdir(parents=True)
    (previous_dir / "index.html").write_text("last good", encoding="utf-8")
    seed_dir = tmp_path / "seed" / "public"
    seed_dir.mkdir(parents=True)
    (seed_dir / "index.html").write_text("older seed", encoding="utf-8")
    (seed_dir.parent / "atlas_snapshots.sqlite").write_bytes(b"seed db")
    monkeypatch.setenv("ATLAS_STATE_ARCHIVE_URL", "")

    server._bootstrap_state(state_dir, state_dir / "public", seed_dir)

    assert (state_dir / "public" / "index.html").read_text() == "last good"
    assert not previous_dir.exists()


def test_runtime_update_failure_retains_last_good_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    index = public_dir / "index.html"
    index.write_text("last good", encoding="utf-8")
    runtime = AtlasRuntime(
        state_dir=tmp_path, public_dir=public_dir, app_root=tmp_path
    )

    def fail_run(**kwargs):
        Path(kwargs["output"]).write_text("partial update", encoding="utf-8")
        raise RuntimeError("feed unavailable")

    monkeypatch.setattr(server, "run", fail_run)

    assert not runtime.update()
    assert index.read_text(encoding="utf-8") == "last good"
    assert runtime.last_success_at is None
    assert runtime.last_error == "RuntimeError: feed unavailable"
    assert runtime.health()["status"] == "ok"
    assert not (tmp_path / ".public-staging").exists()


def test_runtime_update_success_and_duplicate_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    (public_dir / "index.html").write_text("seed", encoding="utf-8")
    runtime = AtlasRuntime(
        state_dir=tmp_path, public_dir=public_dir, app_root=tmp_path
    )

    def successful_run(**kwargs):
        Path(kwargs["output"]).write_text("fresh site", encoding="utf-8")
        return SimpleNamespace(date="2026-07-13")

    monkeypatch.setattr(server, "run", successful_run)

    assert runtime.update()
    assert runtime.last_report_date == "2026-07-13"
    assert runtime.last_success_at is not None
    assert runtime.last_error is None
    assert (public_dir / "index.html").read_text(encoding="utf-8") == "fresh site"
    assert not (tmp_path / ".public-previous").exists()

    runtime._lock.acquire()
    try:
        assert not runtime.update()
    finally:
        runtime._lock.release()
