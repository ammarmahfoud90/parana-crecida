"""What is pushing the river: dam releases, tributary inflow, and ENSO.

The Parana at Corrientes is the sum of what Itaipu lets through, what the
Iguazu adds at Puerto Iguazu, what Yacyreta releases, and what the Paraguay
brings at the confluence. Those are the upstream drivers, and they lead the
middle river by days.

Three kinds of thing end up here, and they are not equally solid:

**Series we hold** (a5, with years of record): Andresito on the Iguazu,
Yacyreta's inflow and release, and the Parana at Confluencia. These carry
history, so a reading can be placed against what is normal.

**Values INA prints but does not serve** (Guaira Porto, Represa Itaipu).
They appear in the daily report's flow table and nowhere in the a5
catalogue, so we have today's number and no history at all. They are
published as a snapshot and labelled as one.

**ENSO** (NOAA CPC's Oceanic Nino Index). Monthly, slow, and the reason the
whole basin is wet this season - but it is a basin-scale context number, not
a driver you can trace to a particular crest. It is shown as context.

Basin rainfall is the obvious missing piece. See the module notes in the
README: it needs a Google Earth Engine or Earthdata credential this project
does not have.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DEFAULT_ONI = Path("data/oni.json")

#: Middle month of each three-month ONI season.
SEASON_MONTH = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}

#: NOAA's thresholds. Five consecutive overlapping seasons at or beyond
#: +/-0.5 make an episode; a single season at +2.16 does not, which is why
#: this labels the *reading* and not the episode.
ENSO_BANDS = (
    (2.0, "el_nino_very_strong"),
    (1.5, "el_nino_strong"),
    (1.0, "el_nino_moderate"),
    (0.5, "el_nino_weak"),
    (-0.5, "neutral"),
    (-1.0, "la_nina_weak"),
    (-1.5, "la_nina_moderate"),
    (-2.0, "la_nina_strong"),
)

#: The gauges that are upstream drivers rather than main-stem readings.
DRIVER_SERIES = (
    "26605",  # Andresito, Iguazu discharge
    "26684",  # Yacyreta afluente
    "26685",  # Yacyreta efluente
    "26690",  # Confluencia
)

#: Flow stations INA prints but does not serve through a5. Matched on the
#: normalised name: the report spells them "Guaira Porto" and "Represa
#: Itaipu" with accents, and comparing raw strings silently matches nothing.
REPORT_ONLY = ("Guaira Porto", "Represa Itaipu")


@dataclass(frozen=True, slots=True)
class OniReading:
    """One three-month season of the Oceanic Nino Index."""

    season: str
    year: int
    anomaly: float
    sst: float | None = None

    @property
    def day(self) -> date:
        """The middle month of the season, as a date.

        DJF belongs to January of its labelled year; NDJ to December. The
        file's year column already follows that convention.
        """
        return date(self.year, SEASON_MONTH[self.season], 1)

    @property
    def phase(self) -> str:
        return classify_enso(self.anomaly)


def classify_enso(anomaly: float | None) -> str:
    """Label one ONI reading. Not an episode: see ``ENSO_BANDS``."""
    if anomaly is None:
        return "unknown"
    for threshold, label in ENSO_BANDS:
        if threshold >= 0 and anomaly >= threshold:
            return label
        if threshold < 0 and anomaly > threshold:
            return "neutral" if label == "neutral" else label
    return "la_nina_very_strong"


def parse_oni(text: str) -> list[OniReading]:
    """Parse NOAA CPC's ``oni.ascii.txt``.

    Four whitespace-separated columns: season, year, total SST, anomaly.
    Malformed lines are skipped rather than guessed at.
    """
    out: list[OniReading] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] not in SEASON_MONTH:
            continue  # the header and anything unexpected
        try:
            out.append(
                OniReading(
                    season=parts[0],
                    year=int(parts[1]),
                    sst=float(parts[2]),
                    anomaly=float(parts[3]),
                )
            )
        except ValueError:
            log.warning("unparseable ONI line: %r", line)
    out.sort(key=lambda r: (r.year, SEASON_MONTH[r.season]))
    return out


def fetch_oni(url: str = ONI_URL, *, timeout: float = 60.0) -> list[OniReading]:
    """Download the ONI series. Raises on a failed request."""
    import requests

    response = requests.get(
        url, timeout=timeout, headers={"User-Agent": "parana-crecida/0.1"}
    )
    response.raise_for_status()
    readings = parse_oni(response.text)
    if not readings:
        raise ValueError(f"no ONI readings parsed from {url}; the format may have changed")
    log.info("ONI: %d seasons, latest %s", len(readings), readings[-1].season)
    return readings


def write_oni(path: Path, readings: list[OniReading], *, source: str = ONI_URL) -> None:
    """Freeze the ONI series. Small, slow-moving, and worth committing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "source": source,
        "fetched_on": date.today().isoformat(),
        "readings": [
            {"season": r.season, "year": r.year, "sst": r.sst, "anomaly": r.anomaly}
            for r in readings
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def load_oni(path: Path | str = DEFAULT_ONI) -> list[OniReading]:
    """The stored ONI series, or empty if it has not been fetched."""
    path = Path(path)
    if not path.exists():
        log.warning("no ONI at %s; ENSO context will be missing", path)
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        OniReading(
            season=r["season"],
            year=int(r["year"]),
            anomaly=float(r["anomaly"]),
            sst=None if r.get("sst") is None else float(r["sst"]),
        )
        for r in payload.get("readings", [])
    ]


def build_payload(
    store,
    clim,
    *,
    oni: list[OniReading] | None = None,
    snapshot: dict | None = None,
    rain: dict | None = None,
    window_days: int = 365,
    now: datetime | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Everything view 3 needs. Pure: builds the dict, writes nothing."""
    from datetime import timedelta

    from ina_a5.observations import DEFAULT_TZ

    from . import gauges as roster

    now = now or datetime.now(DEFAULT_TZ)
    today = today or now.date()
    start = today - timedelta(days=window_days)
    oni = oni if oni is not None else []

    # The official lines, so the sparkline can show where they sit.
    thresholds = {
        row[0]: (row[1], row[2])
        for row in store.con.execute(
            "SELECT series_id, alert, evacuation FROM threshold"
        ).fetchall()
    }

    # The roster's notes are developer annotations in English and must not
    # leak onto a Spanish page; the station name carries the meaning.
    gauges: list[dict[str, object]] = []
    for series_id in DRIVER_SERIES:
        values = store.series(series_id, since=start)
        if not values:
            log.warning("driver %s has no data in the window", series_id)
            continue
        gauge = roster.BY_ID.get(series_id)
        latest = values[-1]
        previous = values[-8] if len(values) >= 8 else None
        gauges.append(
            {
                "series_id": series_id,
                "name": gauge.label if gauge else series_id,
                "unit": "m3/s" if gauge and "flow" in gauge.flags else "m",
                "days": [v.day.isoformat() for v in values],
                "values": [round(v.value, 1) for v in values],
                "latest": {
                    "day": latest.day.isoformat(),
                    "value": round(latest.value, 1),
                    "percentile": (
                        None
                        if (p := clim.percentile_of(series_id, latest.day, latest.value))
                        is None
                        else round(p, 1)
                    ),
                },
                "change_7d": (
                    None if previous is None else round(latest.value - previous.value, 1)
                ),
                "alert": thresholds.get(series_id, (None, None))[0],
                "evacuation": thresholds.get(series_id, (None, None))[1],
            }
        )

    # Stations INA prints but does not serve: today's number, no history.
    from .thresholds import normalise_name

    wanted = {normalise_name(s) for s in REPORT_ONLY}
    printed: list[dict[str, object]] = []
    for row in (snapshot or {}).get("rows", []):
        if normalise_name(row.get("station", "")) in wanted and row.get("value") is not None:
            printed.append(
                {
                    "station": row["station"],
                    "river": row.get("river", ""),
                    "unit": row.get("unit", ""),
                    "value": row["value"],
                    "trend": row.get("trend", "unknown"),
                    "alert": row.get("alert"),
                    "evacuation": row.get("evacuation"),
                }
            )

    # Rainfall is frozen, unlike everything else here, so the page is told
    # how old it is rather than left to imply it is current.
    rainfall = None
    if rain:
        from .rainfall import stale_days, summarise

        rainfall = {
            "source": rain.get("source"),
            "fetched_on": rain.get("fetched_on"),
            "stale_days": stale_days(rain, today),
            "window": rain.get("window"),
            "basins": summarise(rain),
        }

    latest_oni = oni[-1] if oni else None
    recent = [r for r in oni if r.year >= today.year - 3]

    return {
        "schema": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"start": start.isoformat(), "end": today.isoformat()},
        "gauges": gauges,
        "report_only": printed,
        "report_date": (snapshot or {}).get("source_date"),
        "rainfall": rainfall,
        "enso": None
        if latest_oni is None
        else {
            "season": latest_oni.season,
            "year": latest_oni.year,
            "anomaly": latest_oni.anomaly,
            "phase": latest_oni.phase,
            "source": ONI_URL,
            "recent": [
                {"season": r.season, "year": r.year, "anomaly": r.anomaly}
                for r in recent
            ],
        },
    }
