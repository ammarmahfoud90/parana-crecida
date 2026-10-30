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

from . import chain as ch
from . import gauges as roster
from . import thresholds as th
from .chain import DEFAULT_CHAIN
from .lag import DEFAULT_TABLE
from .export import (
    DEFAULT_SITE_DATA,
    ExportBlocked,
    export_drivers,
    export_flooded,
    export_area_stage,
    export_csv,
    export_river_stage,
    export_track,
    export_river_today,
    export_wave,
)
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
    th.write_snapshot(
        th.DEFAULT_SNAPSHOT, rows, source_pdf=pdf_path.name, source_date=source_day
    )

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


# -- chain -----------------------------------------------------------------


@main.command()
@click.option(
    "--rederive",
    is_flag=True,
    help="Walk the river network again and rewrite the chain.",
)
@click.option(
    "--network",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="rios-argentina's network.json. Required with --rederive.",
)
@click.option(
    "--snapped",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="rios-argentina's stations_snapped.json. Required with --rederive.",
)
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_CHAIN, show_default=True
)
@report_option
def chain(
    rederive: bool,
    network: Path | None,
    snapped: Path | None,
    out: Path,
    report: Path | None,
) -> None:
    """Show the downstream chain, or re-derive it from the river network.

    Without --rederive this just prints the committed chain. The chain is
    frozen on purpose: one that silently reordered itself between runs
    would quietly change every travel time on the site, so re-deriving is
    explicit and its output is something you diff.
    """
    rep = Report("chain")

    if rederive:
        if network is None or snapped is None:
            raise click.ClickException("--rederive needs both --network and --snapped")
        nodes, off_chain = ch.derive(network, snapped)
        ch.write_chain(out, nodes, off_chain)
        rep.say(f"derived {len(nodes)} gauges, wrote {out}")
    else:
        nodes = ch.load_chain(out)
        off_chain = []
        if not nodes:
            raise click.ClickException(
                f"no chain at {out}; run with --rederive --network ... --snapped ..."
            )
        rep.say(f"{len(nodes)} gauges from {out}")

    problems = ch.validate_chain(nodes)
    rep.say()
    table = Table(title="Parana chain", header_style="bold")
    for column in ("km", "leg", "series", "gauge", "catchment km2"):
        table.add_column(column, justify="right" if column != "gauge" else "left")
    previous = None
    for node in nodes:
        leg = "" if previous is None else f"+{node.river_km - previous:.0f}"
        previous = node.river_km
        table.add_row(
            f"{node.river_km:.0f}", leg, node.series_id, node.name, f"{node.up_skm:,.0f}"
        )
    console.print(table)
    for node in nodes:
        rep.lines.append(f"  {node.river_km:>7.1f} km  {node.series_id}  {node.name}")

    routing = ch.routing_legs(nodes)
    rep.say()
    rep.say(f"{len(routing)} routing legs (tier 1, correctly snapped):")
    for leg_ in routing:
        rep.say(f"  {leg_.label:<38} {leg_.km:>6.1f} km")

    if off_chain:
        rep.say()
        rep.say(f"{len(off_chain)} gauges in scope are not on the chain:")
        names = [roster.BY_ID[s].name for s in off_chain if s in roster.BY_ID]
        for chunk in range(0, len(names), 4):
            rep.say("  " + ", ".join(names[chunk:chunk + 4]))
        rep.say("  (unrouted or snapped to a tributary reach; no river-km is invented)")

    rep.say()
    if problems:
        for problem in problems:
            rep.say(f"  PROBLEM: {problem}")
        rep.write(report)
        raise click.ClickException(f"{len(problems)} problem(s) with the chain")
    rep.say("chain is sound: river-km increase downstream and catchment never shrinks")
    rep.write(report)


@main.command()
@cache_option
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_TABLE, show_default=True
)
@click.option(
    "--climatology",
    type=click.Path(path_type=Path),
    default=Path("data/climatology_parana.json"),
    show_default=True,
)
@report_option
def lags(
    cache_dirs: Sequence[Path],
    out: Path,
    climatology: Path,
    report: Path | None,
) -> None:
    """Measure travel time between gauges and freeze the table.

    Run this against the FULL record, not the scheduled run's cache. Travel
    times are a climatology drawn from every flood on file; a year of cache
    contains almost no crests and would report none at all. Like the chain,
    the result is committed and the scheduled run reads it.
    """
    from .events import anomaly_series, detect
    from .lag import leg_lag, write_table
    from .normalize import Climatology

    cache = _cache(cache_dirs)
    clim = Climatology.load(climatology)
    nodes = ch.load_chain()
    if not nodes:
        raise click.ClickException("no chain; run `crecida chain --rederive` first")

    rep = Report("travel times")
    crests: dict[str, list] = {}
    anomalies: dict[str, list] = {}
    spans: list[str] = []

    for node in nodes:
        values = cache.read(node.series_id)
        if not values:
            rep.say(f"  {node.name}: nothing cached")
            continue
        crests[node.series_id] = detect(node.series_id, values, clim)
        anomalies[node.series_id] = anomaly_series(node.series_id, values, clim)
        spans.append(f"{values[0].day.year}-{values[-1].day.year}")
        high = sum(1 for c in crests[node.series_id] if c.is_high)
        rep.say(f"  {node.name:<22} {len(values):>7} days  {high:>4} flood crests")

    legs = [leg_lag(leg, crests, anomalies) for leg in ch.routing_legs(nodes)]
    thin = [L for L in legs if L.n < 5]
    if len(thin) == len(legs):
        raise click.ClickException(
            "every leg has fewer than 5 matched waves; this looks like a short "
            "cache rather than a real result. Point --cache-dir at the full record."
        )

    record_span = f"{min(spans)} to {max(spans)}" if spans else ""
    write_table(out, legs, record_span=record_span)

    rep.say()
    rep.say(f"{'leg':<40} {'km':>6} {'n':>4} {'days':>5} {'km/day':>7}  agree")
    for L in legs:
        days = f"{L.median_days:.0f}" if L.median_days is not None else "-"
        cel = f"{L.celerity:.0f}" if not L.resolution_limited and L.celerity else "-"
        agree = "" if L.estimators_agree is None else ("yes" if L.estimators_agree else "NO")
        rep.say(f"  {L.leg.label:<38} {L.km:>6.0f} {L.n:>4} {days:>5} {cel:>7}  {agree}")

    from .lag import fit_celerity

    fit = fit_celerity(legs)
    rep.say()
    if fit:
        rep.say(
            f"whole chain: {fit.total_km:.0f} km in {fit.total_days:.0f} days "
            f"-> {fit.km_per_day:.0f} km/day ({fit.legs_used} legs, "
            f"{fit.legs_skipped} skipped)"
        )
    if thin:
        rep.say(f"{len(thin)} leg(s) rest on fewer than 5 waves: "
                + ", ".join(L.leg.label for L in thin))
    rep.say(f"travel times -> {out}  (record {record_span})")
    rep.write(report)


@main.command()
@click.option(
    "--out", type=click.Path(path_type=Path), default=Path("data/oni.json"),
    show_default=True,
)
@report_option
def oni(out: Path, report: Path | None) -> None:
    """Fetch NOAA CPC's Oceanic Nino Index.

    ENSO is basin-scale context, not a driver you can trace to one crest,
    but it is why the whole basin is wet this season.
    """
    from .drivers import ONI_URL, classify_enso, fetch_oni, write_oni

    rep = Report("oni")
    try:
        readings = fetch_oni()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise click.ClickException(f"could not fetch ONI from {ONI_URL}: {exc}") from exc

    write_oni(out, readings)
    latest = readings[-1]
    rep.say(f"{len(readings)} seasons, {readings[0].year} to {latest.year}")
    rep.say(f"latest: {latest.season} {latest.year}  ONI {latest.anomaly:+.2f}  "
            f"({classify_enso(latest.anomaly)})")
    rep.say()
    rep.say("last 8 seasons:")
    for r in readings[-8:]:
        rep.say(f"  {r.season} {r.year}  {r.anomaly:+.2f}")
    rep.say()
    rep.say(f"oni -> {out}")
    rep.write(report)


@main.command()
@cache_option
@click.option(
    "--source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="rios-argentina's climatology_final.json, to trim from.",
)
@click.option(
    "--out", type=click.Path(path_type=Path),
    default=Path("data/climatology_parana.json"), show_default=True,
)
@report_option
def climatology(
    cache_dirs: Sequence[Path], source: Path | None, out: Path, report: Path | None
) -> None:
    """Build the day-of-year reference curves for the gauges in scope.

    Trims rios-argentina's curves where they exist and computes them from
    the cached record where they do not, so a gauge it never covered - the
    dam and inflow series - can still be placed against its own normal.
    """
    import json as _json

    from .normalize import entry_for, trim

    rep = Report("climatology")
    cache = _cache(cache_dirs)
    wanted = roster.series_ids()

    if source is not None:
        kept = trim(source, wanted, out)
        rep.say(f"trimmed {kept} series from {source.name}")
    existing = _json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}

    built, thin = [], []
    for series_id in wanted:
        if series_id in existing:
            continue
        values = cache.read(series_id)
        gauge = roster.BY_ID[series_id]
        entry = entry_for(
            series_id,
            values,
            station=gauge.name,
            family="flow" if "flow" in gauge.flags else "level",
        )
        if entry is None:
            thin.append(gauge.name)
            continue
        existing[series_id] = entry
        built.append(f"{gauge.name} ({entry['years']} years)")

    out.write_text(
        _json.dumps(existing, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    rep.say(f"{len(existing)} series have a climatology -> {out}")
    if built:
        rep.say(f"built from the cache: {', '.join(built)}")
    if thin:
        rep.say(f"record too short, left without one: {', '.join(thin)}")
    rep.write(report)


@main.command()
@click.option(
    "--project", envvar="EE_PROJECT", default=None,
    help="Earth Engine cloud project id. Also read from EE_PROJECT; a "
         "service-account key carries its own.",
)
@click.option("--window-days", default=180, show_default=True)
@click.option(
    "--out", type=click.Path(path_type=Path), default=Path("data/rainfall.json"),
    show_default=True,
)
@report_option
def rainfall(
    project: str | None, window_days: int, out: Path, report: Path | None
) -> None:
    """Fetch basin rainfall from Earth Engine and freeze it.

    Needs a Google Earth Engine credential, which a scheduled run does not
    have, so this is derived locally and committed. Unlike the chain and
    the travel times it does go stale, and the page says how old it is.
    """
    from .rainfall import BASINS, build, write_table

    rep = Report("rainfall")
    try:
        payload = build(project, window_days=window_days)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise click.ClickException(f"Earth Engine fetch failed: {exc}") from exc

    write_table(out, payload)
    rep.say(f"source {payload['source']}")
    rep.say(f"window {payload['window']['start']} to {payload['window']['end']}")
    rep.say()
    published = {b.key: b.published_km2 for b in BASINS}
    for basin in payload["basins"]:
        values = [v for v in basin["mm"] if v is not None]
        rep.say(
            f"  {basin['key']:<14} {basin['sub_basins']:>4} sub-basins  "
            f"{basin['area_km2']:>9,} km2 (published {published[basin['key']]:,.0f})"
        )
        if values:
            rep.say(
                f"       {len(values)} days, max {max(values):.1f} mm/day, "
                f"last 7 days {sum(values[-7:]):.1f} mm"
            )
    rep.say()
    rep.say(f"rainfall -> {out}")
    rep.write(report)


@main.command()
@click.option(
    "--site-data", type=click.Path(path_type=Path), default=DEFAULT_SITE_DATA,
    show_default=True,
)
@click.option("--max-age-days", default=3, show_default=True)
@click.option("--max-rain-age-days", default=10, show_default=True)
@report_option
def freshness(
    site_data: Path, max_age_days: int, max_rain_age_days: int, report: Path | None
) -> None:
    """Fail if what is published has gone stale.

    The export gate deliberately lets stale data through: if INA is down
    the honest thing is to keep showing the last good reading with its
    date, not to take the site down. But that leaves nobody knowing, so
    this runs last, after the deploy. The site stays up and the run goes
    red, which is what reaches a person.
    """
    import json as _json

    rep = Report("freshness")
    problems: list[str] = []

    river = site_data / "river_today.json"
    if not river.exists():
        raise click.ClickException(f"nothing published at {river}")
    data = _json.loads(river.read_text(encoding="utf-8"))
    age = data.get("data_age_days")
    rep.say(f"river data: {data.get('last_data_day')} ({age} days old)")
    if age is None or age > max_age_days:
        problems.append(f"river data is {age} days old, over the {max_age_days} allowed")

    drivers = site_data / "drivers.json"
    if drivers.exists():
        rain = (_json.loads(drivers.read_text(encoding="utf-8")) or {}).get("rainfall")
        if rain:
            stale = rain.get("stale_days")
            rep.say(f"rainfall:   {rain.get('fetched_on')} ({stale} days old)")
            if stale is None or stale > max_rain_age_days:
                problems.append(
                    f"rainfall is {stale} days old, over the {max_rain_age_days} "
                    "allowed; it needs a local `crecida rainfall` run or a "
                    "service account in CI"
                )
        else:
            rep.say("rainfall:   none published")

    from .export import map_layers_present

    missing = map_layers_present(site_data)
    if missing:
        problems.append(
            "map layers missing from the published directory: " + ", ".join(missing)
        )
    else:
        rep.say("map layers: gauges and departamentos published")

    # The daily river colour is the map's default layer, so if it is absent
    # or behind the map opens on nothing while every other check passes.
    stage = site_data / "river_stage.json"
    if not stage.exists():
        problems.append(f"the map's default layer is not published at {stage}")
    else:
        payload = _json.loads(stage.read_text(encoding="utf-8")) or {}
        days = payload.get("days") or []
        if not days:
            problems.append("the daily river colour has no days to show")
        else:
            newest = date.fromisoformat(days[-1])
            stale = (datetime.now(DEFAULT_TZ).date() - newest).days
            rep.say(f"river line: {days[-1]} ({stale} days old, "
                    f"{len(payload.get('anchors') or [])} anchors)")
            if stale > max_age_days:
                problems.append(
                    f"the daily river colour is {stale} days old, over the "
                    f"{max_age_days} allowed"
                )

    water = site_data / "flooded.json"
    if water.exists():
        flood = _json.loads(water.read_text(encoding="utf-8")) or {}
        if flood.get("stretches"):
            stale = flood.get("stale_days")
            rep.say(f"flooded:    {flood.get('fetched_on')} ({stale} days old)")
            if stale is None or stale > max_rain_age_days:
                problems.append(
                    f"flooded area is {stale} days old, over the "
                    f"{max_rain_age_days} allowed"
                )

    rep.say()
    if problems:
        for problem in problems:
            rep.say(f"  STALE: {problem}")
        rep.write(report)
        raise click.ClickException("; ".join(problems))
    rep.say("everything published is current")
    rep.write(report)


@main.command()
@click.option(
    "--project", envvar="EE_PROJECT", default=None,
    help="Earth Engine cloud project id. Also read from EE_PROJECT.",
)
@click.option("--window-days", default=180, show_default=True)
@click.option(
    "--out", type=click.Path(path_type=Path), default=Path("data/flooded.json"),
    show_default=True,
)
@report_option
def flooded(project: str | None, window_days: int, out: Path, report: Path | None) -> None:
    """Measure flooded area from Sentinel-1 and freeze it.

    Open water from radar backscatter along the river corridor, against a
    baseline taken from the 2021 bajante. Needs an Earth Engine credential.
    """
    from .flooded import WATER_THRESHOLD_DB, build, write_table

    rep = Report("flooded area")
    try:
        payload = build(project, window_days=window_days)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise click.ClickException(f"Earth Engine fetch failed: {exc}") from exc

    write_table(out, payload)
    rep.say(f"source {payload['source']}  threshold {WATER_THRESHOLD_DB} dB")
    rep.say(f"window {payload['window']['start']} to {payload['window']['end']}")
    for stretch in payload["stretches"]:
        values = stretch["km2"]
        rep.say()
        rep.say(f"  {stretch['from_name']} -> {stretch['to_name']}"
                f"  ({stretch['river_km']:.0f} km of river)")
        rep.say(f"    corridor            {stretch['corridor_km2']:>7,} km2")
        rep.say(f"    JRC permanent water {stretch['permanent_water_km2']:>7,.0f} km2")
        rep.say(f"    2021 bajante        {stretch['baseline_km2']:>7,.0f} km2"
                "   <- the calibration check")
        if values:
            rep.say(f"    {len(values)} full passes, latest {values[-1]:,.0f} km2 "
                    f"on {stretch['days'][-1]}, peak {max(values):,.0f} km2")
    rep.say()
    rep.say(f"flooded -> {out}")
    rep.write(report)


@main.command()
@click.option(
    "--project", envvar="EE_PROJECT", default=None,
    help="Earth Engine cloud project id. Also read from EE_PROJECT.",
)
@click.option(
    "--out", type=click.Path(path_type=Path), default=Path("site/media/flood.gif"),
    show_default=True,
)
@click.option("--px", default=1000, show_default=True, help="Longest edge, in pixels.")
@report_option
def animation(project: str | None, out: Path, px: int, report: Path | None) -> None:
    """Render the flood as a loop, one frame per satellite pass.

    Built from the committed flooded-area series, so `crecida flooded` has
    to have run first. Needs an Earth Engine credential.
    """
    from .animation import build

    rep = Report("animation")
    try:
        info = build(project, out=out, px=px)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise click.ClickException(f"could not render the animation: {exc}") from exc

    rep.say(f"{info['frames']} frames, {info['first']} to {info['last']}")
    rep.say(f"peak {info['peak_km2']:,.0f} km2")
    rep.say(f"{info['bytes'] / 1024 / 1024:.2f} MB -> {info['path']}")
    rep.write(report)


@main.command()
@click.option(
    "--project", envvar="EE_PROJECT", default=None,
    help="Earth Engine cloud project id. Also read from EE_PROJECT.",
)
@click.option("--cycles", default=15, show_default=True,
              help="How many 12-day windows to render.")
@click.option("--px", default=1600, show_default=True, help="Longest edge, in pixels.")
@report_option
def mapframes(project: str | None, cycles: int, px: int, report: Path | None) -> None:
    """Render georeferenced water overlays for the map.

    Twelve-day composites rather than single passes: the satellite's swath
    is narrower than this run of river and the corridors sit across swath
    edges, so only a full repeat cycle covers every stretch. Needs an Earth
    Engine credential.
    """
    from . import flooded as _fl
    from .mapframes import render

    rep = Report("map frames")
    try:
        _fl.initialize(project)
        payload = render(cycles=cycles, px=px)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise click.ClickException(f"could not render the map frames: {exc}") from exc

    rep.say(f"{len(payload['frames'])} frames, {payload['window_days']}-day composites")
    rep.say(f"bounds {payload['bounds']}")
    rep.say()
    for frame in payload["frames"]:
        per = "  ".join(f"{k.split('_')[0][:4]}:{v:,.0f}" for k, v in frame["km2"].items())
        rep.say(f"  {frame['start']} .. {frame['end']}  "
                f"{frame['total_km2']:>8,.0f} km2   {per}")
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
            wave = export_wave(store, result, out_dir=out, today=today)
            drivers = export_drivers(store, result, out_dir=out, today=today)
            water = export_flooded(store, result, out_dir=out, today=today)
            river = export_river_stage(store, result, out_dir=out, today=today)
            ahead = export_track(store, result, out_dir=out, today=today)
            tables = export_csv(store, result, out_dir=out, today=today)
            cloud = export_area_stage(result, out_dir=out)
        except ExportBlocked as exc:
            rep.say(f"EXPORT BLOCKED: {exc}")
            rep.write(report)
            raise click.ClickException(str(exc)) from exc

    if cloud is not None:
        rep.say(f"{cloud.gauges} area-against-stage observations -> {cloud.path}")
    for name, rows in tables.items():
        rep.say(f"{rows} rows -> {out / 'csv' / name}")
    rep.say(f"{ahead.gauges} arrival projections -> {ahead.path}")
    rep.say(f"{river.gauges} anchors on the river line -> {river.path}")
    rep.say(f"{water.gauges} river stretches -> {water.path}")
    rep.say(f"{drivers.gauges} driver gauges -> {drivers.path}")
    rep.say(f"{wave.gauges} chain gauges -> {wave.path}")
    rep.say(f"{exported.gauges} gauges -> {exported.path}")
    rep.say(f"{exported.with_official_thresholds} carry official alert/evacuation lines")
    rep.say(f"newest reading: {exported.last_data_day}")
    if result.warnings:
        rep.say(f"warnings: {', '.join(c.name for c in result.warnings)}")
    rep.write(report)


@main.command()
@db_option
@click.option(
    "--climatology", type=click.Path(path_type=Path),
    default=Path("data/climatology_parana.json"), show_default=True,
)
@click.option(
    "--out", type=click.Path(path_type=Path),
    default=None, help="Where to freeze the result.",
)
@click.option("--from-year", default=1998, show_default=True)
@report_option
def backtest(
    db: Path, climatology: Path, out: Path | None, from_year: int, report: Path | None
) -> None:
    """Score the arrival projections against what the river actually did.

    Run over the whole record, as the tracker would have run on each day,
    and compare what it would have published against the crest that then
    arrived. The result is frozen and published beside the projection, so
    the page states its own accuracy.

    This needs the long record, so it is a local command: the scheduled
    run's cache holds about a year. The frozen file is what CI publishes.
    """
    from . import backtest as bt
    from .normalize import Climatology

    rep = Report("backtest")
    clim = Climatology.load(climatology)
    with Store(db, read_only=True) as store:
        payload = bt.run(store, clim, first_year=from_year)

    if not payload.get("scored"):
        raise click.ClickException("nothing could be scored")

    bt.write(out or bt.DEFAULT_OUT, payload)
    rep.say(f"{payload['scored']} projections over {payload['occasions']} occasions")
    rep.say(
        f"inside the published window: {payload['inside_window']}"
        f"/{payload['scored']} ({payload['inside_window_pct']}%)"
    )
    rep.say(f"median error: {payload['median_error_days']} days")
    rep.say(
        f"baseline with no travel times: "
        f"{payload['baseline_median_error_days']} days"
    )
    flat = payload.get("crest_flatness") or {}
    if flat:
        rep.say(
            f"a crest sits within {flat['tolerance_m']*100:.0f} cm of its peak "
            f"for a median of {flat['median_days']} days, so the crest date "
            "is itself only good to a few days"
        )
    rep.say(f"-> {out or bt.DEFAULT_OUT}")
    rep.write(report)


@main.command()
@click.option("--project", default=None, help="Earth Engine project.")
@click.option("--window-days", default=90, show_default=True)
@click.option("--px", default=2048, show_default=True)
@click.option(
    "--out", type=click.Path(path_type=Path),
    default=Path("site/media/basemap"), show_default=True,
)
@report_option
def basemap(
    project: str | None, window_days: int, px: int, out: Path, report: Path | None
) -> None:
    """Render the river from orbit, as a false-colour image per stretch.

    Sentinel-2, cloud-masked and composited: SWIR in red, near infrared in
    green, visible blue in blue. Vegetation reads bright green, water deep
    navy, bare ground pink. A flooded field looks different from a dry one,
    which on a natural-colour image it does not.

    Needs Earth Engine, so it runs here rather than in the scheduled job,
    and the images are committed.
    """
    from . import basemap as bm
    from .rainfall import initialize

    rep = Report("basemap")
    initialize(project=project)
    payload = bm.render(out_dir=out, window_days=window_days, px=px)

    rep.say(f"window {payload['window']['start']} to {payload['window']['end']}")
    rep.say(f"bands: red {payload['bands']['red']}, "
            f"green {payload['bands']['green']}, blue {payload['bands']['blue']}")
    for tile in payload["tiles"]:
        rep.say(f"  {tile['key']:28s} {tile['scenes']:3d} scenes")
    rep.say(f"{len(payload['tiles'])} stretches rendered")
    rep.write(report)


@main.command()
@db_option
@click.option(
    "--out", type=click.Path(path_type=Path),
    default=Path("data/return_periods.json"), show_default=True,
)
@report_option
def frequency(db: Path, out: Path, report: Path | None) -> None:
    """Fit stage-frequency curves, so the site can say how rare this is.

    Log-Pearson III by L-moments on each gauge's annual maxima. Needs the
    long record, so it runs here and the table is committed; the daily job
    reads it.
    """
    import json as _json

    from . import frequency as fq

    rep = Report("frequency")
    with Store(db, read_only=True) as store:
        fits = fq.build(store)
    if not fits:
        raise click.ClickException("no gauge has enough years to fit")

    payload = fq.to_payload(fits)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _json.dumps(payload, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8"
    )
    for f in fits:
        rep.say(
            f"  {f.name:22s} {f.years:3d} yr  1-in-10 {f.levels[10]:6.2f} m  "
            f"1-in-50 {f.levels[50]:6.2f} m  record {f.record_max:6.2f} ({f.record_max_year})"
        )
    rep.say(f"{len(fits)} gauges fitted -> {out}")
    rep.write(report)


@main.command()
@click.option("--project", default=None, help="Earth Engine project.")
@click.option("--cycles", default=15, show_default=True)
@click.option(
    "--out", type=click.Path(path_type=Path),
    default=Path("data/exposure.json"), show_default=True,
)
@report_option
def exposure(project: str | None, cycles: int, out: Path, report: Path | None) -> None:
    """Count who lives where the water now is.

    Modelled residential population on ground the radar found under water
    and the JRC does not map as permanent channel. Not a count of people
    flooded - it is who lives where the water is - and a floor, because
    radar cannot see water under trees.
    """
    from . import exposure as ex
    from .rainfall import initialize

    rep = Report("exposure")
    initialize(project=project)
    payload = ex.build(cycles=cycles)
    ex.write(out, payload)

    latest, peak = payload.get("latest"), payload.get("peak")
    if latest:
        rep.say(f"latest {latest['day']}: {latest['people']:,} people, "
                f"{latest['built_km2']} km2 of built surface")
    if peak:
        rep.say(f"peak   {peak['day']}: {peak['people']:,} people")
    rep.say(f"{len(payload['series'])} composites across "
            f"{len(payload['stretches'])} stretches -> {out}")
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
