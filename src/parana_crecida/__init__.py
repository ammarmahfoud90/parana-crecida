"""Flood-wave tracker for the Argentine Parana.

Detects rises and crests at each gauge, estimates travel time between gauges
from the historical record, and projects where a wave now in progress will
arrive downstream.

Ingestion is not here: it lives in the ``ina_a5`` package, which is generic
and knows nothing about the Parana.
"""

from __future__ import annotations

from .gauges import (
    BY_ID,
    DELTA,
    GAUGES,
    MAIN_STEM,
    TRIBUTARIES,
    UPSTREAM,
    Gauge,
    gauge,
    routing_nodes,
    series_ids,
)

__version__ = "0.1.0"

__all__ = [
    "BY_ID",
    "DELTA",
    "GAUGES",
    "MAIN_STEM",
    "TRIBUTARIES",
    "UPSTREAM",
    "Gauge",
    "gauge",
    "routing_nodes",
    "series_ids",
]
