"""Zeabur/self-hosted runtime for Atlas.

The core product remains static-first: :func:`atlas.pipelines.daily.run` writes a
complete site and JSON contract.  This module adds only the two pieces a
persistent server needs:

* serve the generated directory on ``0.0.0.0:$PORT``;
* refresh it after the US close on weekdays, using New York local time so DST
  does not move the schedule.

Runtime state lives under ``ATLAS_STATE_DIR`` and is intended to be a mounted
volume.  Broker credentials do not belong in this process; a future paper
trading worker must be deployed as a separate private service.
"""
from __future__ import annotations

import functools
import io
import json
import logging
import os
import shutil
import threading
import urllib.request
import zipfile
from datetime import datetime, time, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import config
from .pipelines.daily import run

_LOG = logging.getLogger("atlas.server")
_NY = ZoneInfo("America/New_York")
_DEFAULT_STATE_ARCHIVE = (
    "https://github.com/wind22/Atlas/archive/refs/heads/data.zip"
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _stocks_from_env() -> dict[str, str] | None:
    raw = os.getenv("ATLAS_STOCKS", "").strip()
    if not raw:
        return None
    tickers = [part.strip().upper() for part in raw.split(",") if part.strip()]
    return {ticker: config.name_of(ticker) for ticker in tickers} or None


def next_scheduled_run(
    now: datetime,
    *,
    hour: int = 16,
    minute: int = 30,
    tz: ZoneInfo = _NY,
) -> datetime:
    """Return the next weekday run time in ``tz``.

    ``now`` may use any timezone but must be timezone-aware.  Market holidays
    are deliberately not predicted here; a holiday run simply observes that
    the latest bar has not changed, which is safe for this responsive system.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(tz)
    candidate = datetime.combine(
        local_now.date(), time(hour=hour, minute=minute), tzinfo=tz
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def restore_state_archive(
    payload: bytes,
    *,
    db_path: Path,
    public_data_dir: Path,
) -> bool:
    """Restore the machine-owned ``data`` branch archive without extracting it.

    Reading members individually avoids zip path traversal.  Existing state is
    never overwritten, so a container restart cannot roll the volume backward.
    """
    if db_path.exists():
        return False

    restored_db: bytes | None = None
    restored_data: list[tuple[Path, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            parts = Path(member.filename).parts
            if len(parts) == 2 and parts[-1] == "atlas_snapshots.sqlite":
                restored_db = archive.read(member)
                continue
            if len(parts) >= 3 and parts[1] == "site-data":
                relative = Path(*parts[2:])
                if ".." in relative.parts:
                    continue
                restored_data.append((relative, archive.read(member)))

    if restored_db is None:
        return False

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(restored_db)
    public_data_dir.mkdir(parents=True, exist_ok=True)
    for relative, content in restored_data:
        target = public_data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return True


def _bootstrap_state(state_dir: Path, public_dir: Path, seed_dir: Path) -> None:
    """Initialize an empty volume from the latest data branch and image seed."""
    state_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = public_dir.parent / f".{public_dir.name}-staging"
    previous_dir = public_dir.parent / f".{public_dir.name}-previous"
    shutil.rmtree(staging_dir, ignore_errors=True)
    if previous_dir.is_dir() and not public_dir.exists():
        previous_dir.rename(public_dir)
        _LOG.warning("recovered public site after interrupted publish")
    elif previous_dir.exists():
        shutil.rmtree(previous_dir, ignore_errors=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    # Establish a complete, immediately servable site first.  A successful
    # remote restore below then overwrites only public/data with newer state.
    if not (public_dir / "index.html").exists():
        if not seed_dir.is_dir():
            raise RuntimeError(f"Atlas seed site missing: {seed_dir}")
        shutil.copytree(seed_dir, public_dir, dirs_exist_ok=True)
        _LOG.info("initialized public site from image seed")

    db_path = state_dir / "atlas_snapshots.sqlite"
    archive_url = os.getenv("ATLAS_STATE_ARCHIVE_URL", _DEFAULT_STATE_ARCHIVE).strip()
    if not db_path.exists() and archive_url:
        try:
            request = urllib.request.Request(
                archive_url, headers={"User-Agent": "Atlas-monitor/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
            if restore_state_archive(
                payload, db_path=db_path, public_data_dir=public_dir / "data"
            ):
                _LOG.info("restored prior state from %s", archive_url)
        except Exception:  # noqa: BLE001 - seed failure must degrade gracefully
            _LOG.exception("could not restore remote state; using image seed")

    local_seed_db = seed_dir.parent / "atlas_snapshots.sqlite"
    if not db_path.exists() and local_seed_db.is_file():
        shutil.copy2(local_seed_db, db_path)
        _LOG.info("restored frozen SQLite seed from image")


def _refresh_static_assets(public_dir: Path, app_root: Path) -> None:
    """Copy versioned static assets without touching generated report data."""
    web_dir = app_root / "web"
    if web_dir.is_dir():
        for source in web_dir.iterdir():
            if source.is_file():
                shutil.copy2(source, public_dir / source.name)
    reports_dir = app_root / "reports"
    for name in ("backtest.html", "backtest.json"):
        source = reports_dir / name
        if source.is_file():
            shutil.copy2(source, public_dir / name)


class AtlasRuntime:
    """Own the daily update lock and small, non-sensitive health state."""

    def __init__(self, *, state_dir: Path, public_dir: Path, app_root: Path) -> None:
        self.state_dir = state_dir
        self.public_dir = public_dir
        self.app_root = app_root
        self._lock = threading.Lock()
        self.last_attempt_at: str | None = None
        self.last_success_at: str | None = None
        self.last_report_date: str | None = None
        self.last_error: str | None = None

    def health(self) -> dict[str, object]:
        return {
            "status": "ok" if (self.public_dir / "index.html").is_file() else "starting",
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_report_date": self.last_report_date,
            "last_error": self.last_error,
        }

    def update(self) -> bool:
        if not self._lock.acquire(blocking=False):
            _LOG.warning("daily update already running; duplicate trigger ignored")
            return False
        staging_dir = self.public_dir.parent / f".{self.public_dir.name}-staging"
        previous_dir = self.public_dir.parent / f".{self.public_dir.name}-previous"
        try:
            self.last_attempt_at = datetime.now(timezone.utc).isoformat()
            self.last_error = None
            shutil.rmtree(staging_dir, ignore_errors=True)
            if self.public_dir.is_dir():
                shutil.copytree(self.public_dir, staging_dir)
            else:
                staging_dir.mkdir(parents=True)
            report = run(
                stocks=_stocks_from_env(),
                offline=_env_bool("ATLAS_OFFLINE", False),
                period=os.getenv("ATLAS_PERIOD", "3y"),
                db_path=str(self.state_dir / "atlas_snapshots.sqlite"),
                output=str(staging_dir / "index.html"),
                write_details=_env_bool("ATLAS_WRITE_DETAILS", True),
            )
            _refresh_static_assets(staging_dir, self.app_root)

            # Requests continue reading the old directory while a complete new
            # site is generated.  Directory renames on the mounted Linux volume
            # then make the new contract visible as one publish operation.
            shutil.rmtree(previous_dir, ignore_errors=True)
            if self.public_dir.exists():
                self.public_dir.rename(previous_dir)
            try:
                staging_dir.rename(self.public_dir)
            except Exception:
                if previous_dir.exists() and not self.public_dir.exists():
                    previous_dir.rename(self.public_dir)
                raise
            shutil.rmtree(previous_dir, ignore_errors=True)

            self.last_report_date = report.date
            self.last_success_at = datetime.now(timezone.utc).isoformat()
            _LOG.info("daily update complete: report_date=%s", report.date)
            return True
        except Exception as exc:  # noqa: BLE001 - retain last good static site
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.last_error = f"{type(exc).__name__}: {exc}"
            _LOG.exception("daily update failed; retaining last good site")
            return False
        finally:
            self._lock.release()


def _scheduler_loop(runtime: AtlasRuntime, stop: threading.Event) -> None:
    if _env_bool("ATLAS_RUN_ON_START", True) and not stop.is_set():
        runtime.update()

    hour = int(os.getenv("ATLAS_SCHEDULE_HOUR", "16"))
    minute = int(os.getenv("ATLAS_SCHEDULE_MINUTE", "30"))
    while not stop.is_set():
        target = next_scheduled_run(datetime.now(timezone.utc), hour=hour, minute=minute)
        delay = max(0.0, (target - datetime.now(target.tzinfo)).total_seconds())
        _LOG.info("next daily update: %s", target.isoformat())
        if stop.wait(delay):
            return
        runtime.update()


def _handler_class(runtime: AtlasRuntime, public_dir: Path):
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
            if urlsplit(self.path).path == "/healthz":
                health = runtime.health()
                payload = json.dumps(health, ensure_ascii=False).encode("utf-8")
                status = (
                    HTTPStatus.OK
                    if health["status"] == "ok"
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            super().do_GET()

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            super().end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            _LOG.info("http %s", fmt % args)

    return functools.partial(Handler, directory=str(public_dir))


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app_root = Path(os.getenv("ATLAS_APP_ROOT", Path.cwd())).resolve()
    state_dir = Path(os.getenv("ATLAS_STATE_DIR", "runtime")).resolve()
    public_dir = state_dir / "public"
    seed_dir = Path(os.getenv("ATLAS_SEED_DIR", app_root / "public-seed")).resolve()

    _bootstrap_state(state_dir, public_dir, seed_dir)
    _refresh_static_assets(public_dir, app_root)
    runtime = AtlasRuntime(state_dir=state_dir, public_dir=public_dir, app_root=app_root)

    stop = threading.Event()
    scheduler = threading.Thread(
        target=_scheduler_loop,
        args=(runtime, stop),
        name="atlas-daily-scheduler",
        daemon=True,
    )
    scheduler.start()

    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _handler_class(runtime, public_dir))
    _LOG.info("serving %s on 0.0.0.0:%s", public_dir, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
