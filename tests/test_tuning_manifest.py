"""Calibration and evaluation have to stay apart, and be seen to.

A mask scored on the data its parameters were fitted to reports how well
it fits, not how it behaves. This project does exactly that - one date,
and three of the nine reaches, shared between `tune_mask.py` and
`validate_mask.py` - and nothing said so anywhere, while the per-reach
score from that fit decided which reaches publish an area at all.

`data/tuning_manifest.json` is the record. These tests hold it to three
things: that it describes the parameters actually in production, that it
admits the leakage rather than hiding it, and that any held-out set added
later really is held out.

The last one is the point. Today the manifest's held-out lists are empty
and these tests pass trivially. The moment someone adds a date to run a
real evaluation, the test starts doing work - and fails if that date was
one of the tuning dates.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

MANIFEST = Path("data/tuning_manifest.json")

pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="no tuning manifest")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def days(entries):
    return {date.fromisoformat(d) for d in entries}


def test_the_manifest_describes_the_parameters_in_production(manifest):
    """A record of a different mask would be worse than none.

    If a parameter moves and this is not updated, the manifest stops
    describing what is published and starts describing history.
    """
    from parana_crecida import floodwater as fw

    assert manifest["parameters"] == {
        "vh_threshold_db": fw.VH_THRESHOLD_DB,
        "hand_limit_m": fw.HAND_LIMIT_M,
        "vh_floor_db": fw.VH_FLOOR_DB,
    }


def test_the_manifest_names_the_scripts_that_set_and_score_the_parameters(manifest):
    assert Path(manifest["tuning"]["script"]).exists()
    assert Path(manifest["scoring"]["script"]).exists()
    assert Path(manifest["scoring"]["output"]).exists()


def test_the_tuning_reaches_are_real_reaches(manifest):
    """A typo here would silently empty the leakage check below."""
    from parana_crecida import flooded as fl

    keys = {s.key for s in fl.load_corridors()}
    assert keys, "no corridors committed"
    assert set(manifest["tuning"]["reaches"]) <= keys


def test_the_published_score_is_declared_in_sample(manifest):
    """Because it is, and because nothing else says so.

    data/mask_skill.json carries the figure the front page quotes and the
    figure the publication gate uses. Both come from the same date the
    parameters were fitted on.
    """
    scoring = manifest["scoring"]
    assert scoring["in_sample"] is True
    assert days(scoring["dates"]) & days(manifest["tuning"]["dates"]), (
        "the manifest claims the scoring is in-sample but shares no date "
        "with the tuning - one of the two is wrong"
    )


def test_the_score_records_what_it_does_not_cover(manifest):
    """The three limits, kept with the number rather than beside it."""
    not_scored = " ".join(manifest["scoring"]["not_scored"]).lower()
    assert "flood_km2" in not_scored
    assert "vegetation" in not_scored
    assert manifest["scoring"]["scored_quantity"] == "total water"


def test_the_manifest_admits_the_parameters_are_not_reproducible(manifest):
    """They are not, and a record that implied otherwise would mislead.

    The sweep never covered -25 dB, never swept HAND at all, and did not
    run between 73a539c and the repair. Extending it afterwards does not
    retrospectively justify the values in use.
    """
    assert manifest["reproducible"] is False
    assert manifest["reproducible_note"].strip()


def test_no_held_out_date_was_a_tuning_date(manifest):
    """The whole point of the file.

    Empty today, so this passes trivially - and starts doing work the
    moment a real evaluation is set up, which is exactly when it is needed.
    """
    tuning = days(manifest["tuning"]["dates"])
    held = days(manifest["held_out"]["dates"])
    assert not (tuning & held), (
        f"held-out dates reuse tuning dates: {sorted(tuning & held)}"
    )


def test_no_held_out_date_falls_inside_a_tuning_window(manifest):
    """A date a week away shares radar passes with the tuning date.

    The reference is a median over six days either side, and the radar
    composite is twelve days wide, so "a different date" has to mean far
    enough away that no acquisition is in both.
    """
    span = max(manifest["tuning"]["date_window_days"], 12)
    tuning = days(manifest["tuning"]["dates"])
    too_close = [
        (h.isoformat(), t.isoformat())
        for h in days(manifest["held_out"]["dates"])
        for t in tuning
        if abs((h - t).days) <= span
    ]
    assert not too_close, f"held-out dates within {span} days of tuning: {too_close}"


def test_no_held_out_reach_was_a_tuning_reach(manifest):
    tuning = set(manifest["tuning"]["reaches"])
    held = set(manifest["held_out"]["reaches"])
    assert not (tuning & held), (
        f"held-out reaches were tuned on: {sorted(tuning & held)}"
    )


def test_the_tuning_and_scoring_scripts_still_share_their_default_date():
    """The leakage is a fact about the code, so read it from the code.

    If someone changes a default, this fails and the manifest has to be
    brought back into line - which is the only way a record like this
    stays true.
    """
    import re

    def default_day(path):
        text = Path(path).read_text(encoding="utf-8")
        match = re.search(r'"--day",\s*default="([0-9-]+)"', text)
        assert match, f"{path} has no --day default to read"
        return match.group(1)

    tune = default_day("scripts/tune_mask.py")
    score = default_day("scripts/validate_mask.py")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert tune in manifest["tuning"]["dates"], (
        f"tune_mask.py tunes on {tune}, which the manifest does not list"
    )
    assert score in manifest["scoring"]["dates"], (
        f"validate_mask.py scores on {score}, which the manifest does not list"
    )
    # And the thing the manifest exists to record.
    assert tune == score, (
        "tuning and scoring no longer share a date - if that is deliberate, "
        "the manifest's in_sample flag and its note need updating with it"
    )


def test_the_tuning_reaches_are_the_ones_the_sweep_defaults_to():
    """Same argument: the manifest must match the script, not a memory."""
    import re

    text = Path("scripts/tune_mask.py").read_text(encoding="utf-8")
    match = re.search(r'"--keys",\s*default="([^"]+)"', text)
    assert match, "tune_mask.py has no --keys default to read"
    swept = set(match.group(1).split(","))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert swept == set(manifest["tuning"]["reaches"])


def test_an_evaluation_date_outside_the_manifest_is_what_counts():
    """A worked example of the rule, so the intent cannot be misread.

    This does not touch the real manifest; it checks the comparison that
    the tests above apply to it.
    """
    tuning = {date(2026, 10, 22)}
    span = 12

    def leaks(day):
        return any(abs((day - t).days) <= span for t in tuning)

    assert leaks(date(2026, 10, 22))            # the tuning date itself
    assert leaks(date(2026, 10, 28))            # six days later: shared passes
    assert leaks(date(2026, 10, 22) + timedelta(days=12))
    assert not leaks(date(2026, 10, 22) + timedelta(days=13))
    assert not leaks(date(2021, 9, 1))          # the bajante, years away
