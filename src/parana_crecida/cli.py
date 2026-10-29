"""The ``crecida`` command line.

Only the ingestion and inspection commands exist so far: ``gauges``,
``catalogue``, ``fetch`` and ``guards``. The analysis commands (``events``,
``lags``, ``track``, ``site``) arrive with those modules - they are not
stubbed here, so that nothing looks available before it works.

Every command that touches data writes a plain-text run report alongside its
output, the convention rios-argentina uses and its README tells you to read
before committing.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import click
from ina_a5 import (
    A5Client,
    A5Error,
    DailyValue,
    GuardThresholds,
    SeriesCache,
    fetch_catalogue,
    fetch_daily,
    fetch_history,
    run_guards,
)
from ina_a5.observations import DEFAULT_TZ
from rich.console import Console
from rich.table import Table

from . import gauges as roster
from . import thresholds as th
from .export import DEFAULT_SITE_DATA, ExportBlocked, export_river_today
from .store import DEFAULT_DB, Store
from .validate import validate as run_validate

console = Console()

DEFAULT_CACHE_DIRS = ("cache", "cache_recent")
#: The server belongs to a public agency. Keep this low.
DEFAULT_WORKERS = 4
DEFAULT_THRESHOLDS_CSV = Path("data/ina_thresholds.csv")
#: Daily report PDFs are 2.7 MB each and regenerable, so they stay untracked.
DEFAULT_PDF_DIR = Path(".reports")


class Report:
    """Accumulates a run report and writes it out.

    The rios-argentina scripts each grow a ``lines`` list through a local
    ``say()``; this is the same idea with the printing separated from the
    recording, so ``--quiet`` does not also empty the report.
    """

    def __init__(self, title: str) -> None:
        self.lines: list[str] = [
            f"{title} - {datetime.now(DEFAULT_TZ).isoformat(timespec='seconds')}",
            "",
        ]

    def say(self, text: str = "", *, echo: bool = True) -> None:
        self.lines.append(text)
        if echo:
            console.print(text, highlight=False)

    def write(self, path: Path | None) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        console.print(f"[dim]report -> {path}[/dim]")


def _resolve_series(series: Sequence[str], tiers: Sequence[int]) -> list[str]:
    """Explicit ``--series`` wins over ``--tier``."""
    if series:
        return [str(s) for s in series]
    return roster.series_ids(tiers=tuple(tiers))


class ThreadClients:
    """One :class:`A5Client` per worker thread.

    ``requests.Session`` is not thread-safe, and the original climatology
    build shared a single session across its whole pool.
    """

    def __init__(self, base: A5Client) -> None:
        self._base = base
        self._local = threading.local()
        self._all: list[A5Client] = []
        self._lock = threading.Lock()

    def get(self) -> A5Client:
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._base.clone()
            self._local.client = client
            with self._lock:
                self._all.append(client)
        return client

    def close(self) -> None:
        with self._lock:
            for client in self._all:
                client.close()
            self._all.clear()


# -- shared options --------------------------------------------------------

cache_option = click.option(
    "--cache-dir",
    "cache_dirs",
    multiple=True,
    type=click.Path(path_type=Path),
    help=(
        "Cache root, repeatable and searched in order. Point the first one at "
        "an existing rios-argentina cache to reuse its history. "
        f"Default: {' then '.join(DEFAULT_CACHE_DIRS)}."
    ),
)
tier_option = click.option(
    "--tier",
    "tiers",
    multiple=True,
    type=click.IntRange(1, 3),
    help="Restrict to these gauge tiers. Default: all.",
)
series_option = click.option(
    "--series", multiple=True, help="One or more series ids, overriding --tier."
)
report_option = click.option(
    "--report", type=click.Path(path_type=Path), help="Write a run report here."
)


def _cache(cache_dirs: Sequence[Path]) -> SeriesCache:
    roots = list(cache_dirs) or [Path(d) for d in DEFAULT_CACHE_DIRS]
    return SeriesCache(roots, write_root=roots[0])


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-v", "--verbose", count=True, help="-v for info, -vv for debug.")
@click.version_option(package_name="parana-crecida")
def main(verbose: int) -> None:
    """Track flood waves down the Argentine Parana."""
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbose, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


# -- gauges ----------------------------------------------------------------


@main.command()
@tier_option
def gauges(tiers: Sequence[int]) -> None:
    """Show the gauges in scope and how far each one is trusted."""
    wanted = set(tiers) or {1, 2, 3}
    table = Table(title="Parana gauges in scope", header_style="bold")
    for column in ("series", "gauge", "branch", "tier", "flags", "note"):
        table.add_column(column)
    for g in roster.GAUGES:
        if g.tier not in wanted:
            continue
        table.add_row(
            g.series_id,
            g.name,
            g.branch,
            str(g.tier),
            ", ".join(sorted(g.flags)),
            g.note,
        )
    console.print(table)
    nodes = roster.routing_nodes()
    console.print(
        f"{len(nodes)} routing nodes (tier 1, correctly snapped): "
        + " -> ".join(g.name for g in nodes)
    )


# -- catalogue -------------------------------------------------------------


@main.command()
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=Path("data/a5_catalogue.json"),
    show_default=True,
)
@click.option("--all", "keep_all", is_flag=True, help="Keep non-hydro series too.")
@report_option
def catalogue(out: Path, keep_all: bool, report: Path | None) -> None:
    """Page the whole a5 series catalogue and save it.

    Pages with limit/offset: the unparameterised call silently caps at 5000
    features, and pages sometimes overlap, so this stops on "no new ids".
    """
    rep = Report("a5 catalogue")
    try:
        with A5Client() as client:
            series = fetch_catalogue(client, hydro_only=not keep_all, in_bbox=not keep_all)
    except A5Error as exc:
        raise click.ClickException(f"catalogue fetch failed: {exc}") from exc

    rep.say(f"{len(series)} series kept")
    families: dict[str, int] = {}
    for s in series:
        families[s.family or "other"] = families.get(s.family or "other", 0) + 1
    for family, count in sorted(families.items()):
        rep.say(f"  {family:<6} {count}")

    in_scope = [s for s in series if s.series_id in roster.BY_ID]
    rep.say(f"{len(in_scope)} of the {len(roster.GAUGES)} gauges in scope were found")
    missing = sorted(set(roster.BY_ID) - {s.series_id for s in series})
    if missing:
        rep.say(f"not in the catalogue: {', '.join(missing)}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            [
                {
                    "series_id": s.series_id,
                    "station_id": s.station_id,
                    "station": s.station,
                    "river": s.river,
                    "var_id": s.var_id,
                    "family": s.family,
                    "network": s.network,
                    "lon": s.lon,
                    "lat": s.lat,
                    "cat_start": s.cat_start.isoformat() if s.cat_start else None,
                    "cat_end": s.cat_end.isoformat() if s.cat_end else None,
                }
                for s in series
            ],
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    rep.say(f"catalogue -> {out}")
    rep.write(report)


# -- fetch -----------------------------------------------------------------


@main.command()
@cache_option
@tier_option
@series_option
@click.option(
    "--history",
    is_flag=True,
    help="Download the full record instead of topping up. Slow; run once.",
)
@click.option(
    "--start",
    type=click.DateTime(["%Y-%m-%d"]),
    default="1900-01-01",
    show_default=True,
    help="Earliest day to ask for, with --history.",
)
@click.option("--workers", default=DEFAULT_WORKERS, show_default=True)
@click.option("--dry-run", is_flag=True, help="Fetch and report, but do not write.")
@report_option
def fetch(
    cache_dirs: Sequence[Path],
    tiers: Sequence[int],
    series: Sequence[str],
    history: bool,
    start: datetime,
    workers: int,
    dry_run: bool,
    report: Path | None,
) -> None:
    """Bring the cached series up to date from the a5 API.

    Tops up incrementally by default, re-asking for ten days of overlap so
    late corrections land. A failed series is reported and does not stop the
    run, but it is never silently recorded as empty.
    """
    cache = _cache(cache_dirs)
    wanted = _resolve_series(series, tiers or (1, 2, 3))
    today = datetime.now(DEFAULT_TZ).date()
    rep = Report("fetch")
    rep.say(f"{len(wanted)} series, cache roots: {', '.join(str(r) for r in cache.roots)}")
    rep.say(f"mode: {'full history' if history else 'incremental top-up'}, today {today}")
    rep.say()

    base = A5Client()
    clients = ThreadClients(base)
    failures: list[tuple[str, str]] = []
    updated = 0
    unchanged = 0

    def work(series_id: str) -> tuple[str, int, int, str | None]:
        client = clients.get()
        before = len(cache.read(series_id))
        try:
            if history:
                values: list[DailyValue] = fetch_history(
                    client, series_id, start.date(), today, today=today
                )
            else:
                window_start, window_end = cache.top_up_window(series_id, today)
                values = fetch_daily(
                    client, series_id, window_start, window_end, today=today
                )
        except A5Error as exc:
            return series_id, before, 0, str(exc)
        merged = cache.merge(series_id, values) if not history else values
        if not dry_run and merged:
            cache.write(series_id, merged)
        return series_id, before, len(merged), None

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, sid): sid for sid in wanted}
            for future in as_completed(futures):
                series_id, before, after, error = future.result()
                name = roster.BY_ID[series_id].name if series_id in roster.BY_ID else series_id
                if error:
                    failures.append((series_id, error))
                    rep.say(f"  FAIL  {series_id} {name}: {error}")
                elif after > before:
                    updated += 1
                    rep.say(f"  ok    {series_id} {name}: {before} -> {after} days")
                else:
                    unchanged += 1
    finally:
        clients.close()
        base.close()

    rep.say()
    rep.say(f"{updated} updated, {unchanged} unchanged, {len(failures)} failed")
    if dry_run:
        rep.say("DRY RUN - nothing was written")
    rep.write(report)

    if failures and len(failures) == len(wanted):
        # Every series failing is an outage or a bad token, not bad gauges.
        raise click.ClickException(f"all {len(wanted)} series failed; first: {failures[0][1]}")


# -- guards ----------------------------------------------------------------


@main.command()
@cache_option
@tier_option
@series_option
@click.option("--as-of", type=click.DateTime(["%Y-%m-%d"]), help="Judge as of this day.")
@report_option
def guards(
    cache_dirs: Sequence[Path],
    tiers: Sequence[int],
    series: Sequence[str],
    as_of: datetime | None,
    report: Path | None,
) -> None:
    """Check the cached series for stale, stuck, spiking or re-zeroed gauges.

    Run this before trusting any wave analysis: a datum step reads as a four
    metre overnight flood, which is exactly the signal the tracker hunts for.
    """
    cache = _cache(cache_dirs)
    wanted = _resolve_series(series, tiers or (1, 2, 3))
    today: date = as_of.date() if as_of else datetime.now(DEFAULT_TZ).date()
    thresholds = GuardThresholds()
    rep = Report("guards")
    rep.say(f"{len(wanted)} series as of {today}")
    rep.say()

    clean = 0
    flagged: dict[str, list[str]] = {}
    for series_id in wanted:
        values = cache.read(series_id)
        name = roster.BY_ID[series_id].name if series_id in roster.BY_ID else series_id
        if not values:
            flagged[series_id] = ["not cached"]
            rep.say(f"  ----  {series_id} {name}: not cached")
            continue
        flags = run_guards(values, today, thresholds)
        if not flags:
            clean += 1
            continue
        flagged[series_id] = [f.code for f in flags]
        for flag in flags:
            rep.say(f"  {flag.code:<11} {series_id} {name}: {flag.detail}")

    rep.say()
    rep.say(f"{clean} clean, {len(flagged)} flagged")
    rep.write(report)


# -- thresholds ------------------------------------------------------------


@main.command()
@click.option(
    "--date",
    "report_date",
    type=click.DateTime(["%Y-%m-%d"]),
    help="Which daily report to read. Default: today, falling back day by day.",
)
@click.option(
    "--pdf",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Parse this local PDF instead of downloading one.",
)
@click.option(
    "--pdf-dir", type=click.Path(path_type=Path), default=DEFAULT_PDF_DIR, show_default=True
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=DEFAULT_THRESHOLDS_CSV,
    show_default=True,
)
@click.option("--back-days", default=7, show_default=True, help="How far to fall back.")
@report_option
def thresholds(
    report_date: datetime | None,
    pdf: Path | None,
    pdf_dir: Path,
    out: Path,
    back_days: int,
    report: Path | None,
) -> None:
    """Parse INA's alert and evacuation levels into a CSV.

    The a5 API does not publish thresholds; they are only in the daily
    report PDF. No value is ever invented: a gauge INA does not list gets an
    empty row saying so, and report rows that match no gauge in scope are
    written out too, so the CSV can be checked against the PDF line by line.
    """
    import requests

    rep = Report("ina thresholds")

    if pdf is not None:
        pdf_path, source_day = pdf, report_date.date() if report_date else date.today()
        rep.say(f"reading local PDF {pdf_path}")
    else:
        pdf_dir.mkdir(parents=True, exist_ok=True)
        first = report_date.date() if report_date else date.today()
        pdf_path = None
        source_day = first
        session = requests.Session()
        session.headers["User-Agent"] = "parana-crecida/0.1 (hydrology research)"
        for back in range(back_days + 1):
            day = first - timedelta(days=back)
            url = th.report_url(day)
            try:
                response = session.get(url, timeout=120)
            except requests.RequestException as exc:
                rep.say(f"  {day}: {type(exc).__name__}")
                continue
            # A missing report answers 404 with a short error page, so size
            # is as much a signal as status here.
            if response.status_code == 200 and len(response.content) > 50_000:
                pdf_path = pdf_dir / f"reporte_diario_{day.isoformat()}.pdf"
                pdf_path.write_bytes(response.content)
                source_day = day
                rep.say(f"  {day}: {len(response.content) // 1024} KB -> {pdf_path.name}")
                break
            rep.say(f"  {day}: HTTP {response.status_code}, {len(response.content)} bytes")
        if pdf_path is None:
            raise click.ClickException(
                f"no daily report found between {first - timedelta(days=back_days)} and {first}"
            )

    rows = th.parse_report(pdf_path)
    if not rows:
        raise click.ClickException(
            f"found no threshold tables in {pdf_path}; the report layout may have changed"
        )
    rep.say(f"parsed {len(rows)} rows from the report's threshold tables")

    csv_rows = th.build_rows(rows, source_pdf=pdf_path.name, source_date=source_day)
    th.write_csv(out, csv_rows)

    by_status: dict[str, list[str]] = {}
    for row in csv_rows:
        label = row["gauge"] or row["ina_station"]
        by_status.setdefault(row["status"], []).append(label)

    rep.say()
    official = by_status.get(th.STATUS_OFFICIAL, [])
    rep.say(f"{len(official)} gauges in scope have official levels:")
    for name in official:
        entry = next(r for r in csv_rows if (r["gauge"] or r["ina_station"]) == name)
        rep.say(f"  {name:<22} alert {entry['alert'] or '-':>8}  "
                f"evac {entry['evacuation'] or '-':>8}  {entry['unit']}")

    absent = by_status.get(th.STATUS_NOT_IN_REPORT, [])
    rep.say()
    rep.say(f"{len(absent)} gauges in scope are NOT in the report, left empty:")
    for chunk in range(0, len(absent), 4):
        rep.say("  " + ", ".join(absent[chunk:chunk + 4]))

    empty = by_status.get(th.STATUS_NO_VALUE, [])
    if empty:
        rep.say()
        rep.say(f"{len(empty)} gauges are listed but carry no parseable value: "
                + ", ".join(empty))

    extra = by_status.get(th.STATUS_UNMATCHED, [])
    rep.say()
    rep.say(f"{len(extra)} report rows match no gauge in scope (kept for checking):")
    for chunk in range(0, len(extra), 3):
        rep.say("  " + ", ".join(extra[chunk:chunk + 3]))

    rep.say()
    rep.say(f"thresholds -> {out}  (check it against {pdf_path.name})")
    rep.write(report)


# -- store -----------------------------------------------------------------

db_option = click.option(
    "--db", type=click.Path(path_type=Path), default=DEFAULT_DB, show_default=True
)
thresholds_option = click.option(
    "--thresholds",
    "thresholds_csv",
    type=click.Path(path_type=Path),
    default=DEFAULT_THRESHOLDS_CSV,
    show_default=True,
)


@main.command()
@db_option
@cache_option
@tier_option
@series_option
@thresholds_option
@report_option
def load(
    db: Path,
    cache_dirs: Sequence[Path],
    tiers: Sequence[int],
    series: Sequence[str],
    thresholds_csv: Path,
    report: Path | None,
) -> None:
    """Load the raw JSON cache into DuckDB, with gauges and thresholds.

    The JSON cache stays the raw layer; this is the clean one. A series is
    replaced wholesale, so re-running after a top-up cannot double-count.
    """
    cache = _cache(cache_dirs)
    wanted = _resolve_series(series, tiers or (1, 2, 3))
    rep = Report("load")
    rep.say(f"db {db}, cache roots: {', '.join(str(r) for r in cache.roots)}")

    with Store(db) as store:
        n_gauges = store.load_gauges()
        n_series, n_rows = store.load_observations(cache, wanted, note="crecida load")
        loaded = th.load_csv(thresholds_csv)
        n_thresholds = store.load_thresholds(loaded)
        last = store.last_data_day()

    rep.say(f"{n_gauges} gauges, {n_series} series, {n_rows} observation rows")
    rep.say(f"{n_thresholds} gauges with official thresholds from {thresholds_csv}")
    rep.say(f"newest reading: {last}")
    if n_series < len(wanted):
        rep.say(f"{len(wanted) - n_series} requested series had nothing cached")
    rep.write(report)


@main.command("validate")
@db_option
@click.option("--as-of", type=click.DateTime(["%Y-%m-%d"]), help="Judge as of this day.")
@report_option
def validate_cmd(db: Path, as_of: datetime | None, report: Path | None) -> None:
    """Run the data tests. Exits non-zero if any error-level test fails."""
    today = as_of.date() if as_of else datetime.now(DEFAULT_TZ).date()
    rep = Report("validate")
    with Store(db, read_only=True) as store:
        result = run_validate(store, today=today)

    for line in result.lines():
        rep.say(line)
    rep.say()
    rep.say(
        f"{len(result.failures)} failed, {len(result.warnings)} warnings, "
        f"{len(result.checks)} checks"
    )
    for check in result.failures:
        for offender in check.offenders[:10]:
            rep.say(f"    {check.name}: {offender}")
    rep.write(report)

    if not result.ok:
        raise click.ClickException(
            f"{len(result.failures)} data test(s) failed; nothing should be exported"
        )


# -- export ----------------------------------------------------------------


@main.command()
@db_option
@thresholds_option
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_SITE_DATA, show_default=True
)
@click.option("--as-of", type=click.DateTime(["%Y-%m-%d"]), help="Judge as of this day.")
@report_option
def export(
    db: Path,
    thresholds_csv: Path,
    out: Path,
    as_of: datetime | None,
    report: Path | None,
) -> None:
    """Validate, then write the site's data files.

    If a data test fails nothing is written, so whatever is already
    published stays published, with its own last-update date on the page.
    """
    today = as_of.date() if as_of else datetime.now(DEFAULT_TZ).date()
    rep = Report("export")
    loaded = th.load_csv(thresholds_csv)

    with Store(db, read_only=True) as store:
        result = run_validate(store, today=today)
        for line in result.lines():
            rep.say(line)
        rep.say()
        try:
            exported = export_river_today(store, loaded, result, out_dir=out, today=today)
        except ExportBlocked as exc:
            rep.say(f"EXPORT BLOCKED: {exc}")
            rep.write(report)
            raise click.ClickException(str(exc)) from exc

    rep.say(f"{exported.gauges} gauges -> {exported.path}")
    rep.say(f"{exported.with_official_thresholds} carry official alert/evacuation lines")
    rep.say(f"newest reading: {exported.last_data_day}")
    if result.warnings:
        rep.say(f"warnings: {', '.join(c.name for c in result.warnings)}")
    rep.write(report)


# -- daily -----------------------------------------------------------------


@main.command()
@db_option
@cache_option
@thresholds_option
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_SITE_DATA, show_default=True
)
@click.option("--workers", default=DEFAULT_WORKERS, show_default=True)
@click.option("--skip-fetch", is_flag=True, help="Use the cache as it is.")
@report_option
@click.pass_context
def daily(
    ctx: click.Context,
    db: Path,
    cache_dirs: Sequence[Path],
    thresholds_csv: Path,
    out: Path,
    workers: int,
    skip_fetch: bool,
    report: Path | None,
) -> None:
    """The scheduled run: fetch, load, test, export.

    One command so that CI has one thing to call and one exit code to read.
    A failed data test stops it before the export, which is the point.
    """
    if not skip_fetch:
        console.rule("[bold]fetch")
        ctx.invoke(
            fetch,
            cache_dirs=tuple(cache_dirs),
            tiers=(),
            series=(),
            history=False,
            start=datetime(1900, 1, 1),
            workers=workers,
            dry_run=False,
            report=None,
        )

    console.rule("[bold]load")
    ctx.invoke(
        load,
        db=db,
        cache_dirs=tuple(cache_dirs),
        tiers=(),
        series=(),
        thresholds_csv=thresholds_csv,
        report=None,
    )

    console.rule("[bold]test and export")
    ctx.invoke(
        export, db=db, thresholds_csv=thresholds_csv, out=out, as_of=None, report=report
    )


if __name__ == "__main__":  # pragma: no cover
    main()
