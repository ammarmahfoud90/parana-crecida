"""Shared fixtures, and the line between the suite and the outside world.

The automated suite is deterministic and offline. It never calls the a5
API, never touches Earth Engine, and never reads a file it did not write
or find committed. That is what makes it safe to run on every push and
what makes a red run mean something.

Some checks genuinely cannot be that. Scoring the water mask needs Earth
Engine and Sentinel-2 imagery; confirming that a corridor was observed
needs the radar archive. Those are marked and skipped by default rather
than left out, so the command to run them is discoverable and so nobody
is tempted to approximate them with a fake that proves nothing:

    pytest -m needs_ee --run-external      # needs EE_SERVICE_ACCOUNT_KEY
    pytest -m needs_network --run-external

A marked test that is skipped says so in the run summary. A marked test
that is *run* is expected to be slow and to cost money, which is why it
takes an explicit flag as well as a credential.
"""

from __future__ import annotations

import os

import pytest

EXTERNAL = ("needs_ee", "needs_network")


def pytest_addoption(parser):
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="Run the tests that need Earth Engine or network access.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_ee: needs an Earth Engine credential and quota")
    config.addinivalue_line(
        "markers", "needs_network: needs to reach a live service")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-external"):
        missing = not (os.environ.get("EE_SERVICE_ACCOUNT_KEY")
                       or os.environ.get("EARTHENGINE_TOKEN"))
        if not missing:
            return
        skip = pytest.mark.skip(
            reason="--run-external given but no EE credential in the environment")
        for item in items:
            if "needs_ee" in item.keywords:
                item.add_marker(skip)
        return

    skip = pytest.mark.skip(reason="external; run with --run-external")
    for item in items:
        if any(mark in item.keywords for mark in EXTERNAL):
            item.add_marker(skip)
