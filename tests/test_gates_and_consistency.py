"""Two things nothing was checking: the gate, and the paperwork.

**The gate.** The export refuses to write when a data test fails, so a bad
day cannot reach the page. The refusal is only worth anything if it leaves
what is already published untouched - a gate that blocks and then
truncates the file is worse than no gate, because the site goes wrong and
the run goes red at the same time, which reads as one fault.

**The paperwork.** Every number on the site is also written down in three
other places: the constant in the code, the metadata in the exported file,
and the prose in the README, `docs/METHOD.md` and the method page. They
drifted, repeatedly, and each drift was invisible until somebody read two
of them side by side. These tests read them side by side.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SITE = Path("site/data")
DOCS = Path("docs/METHOD.md")
README = Path("README.md")
METHOD_PAGE = Path("site/method.html")


class Failing:
    """A validation result with an error-level failure."""

    ok = False

    class _Check:
        name = "a check"

    failures = (_Check(),)


# -- the gate ------------------------------------------------------------


def published(tmp_path):
    """A directory standing in for what is already on the site."""
    out = tmp_path / "site"
    (out / "csv").mkdir(parents=True)
    good = {"schema": 1, "marker": "the last good export"}
    for name in ("river_today.json", "floodwater.json", "flooded.json",
                 "wave.json", "drivers.json", "track.json", "river_stage.json"):
        (out / name).write_text(json.dumps(good), encoding="utf-8")
    (out / "csv" / "river_daily.csv").write_text("day,value\n", encoding="utf-8")
    return out


@pytest.mark.parametrize("name", [
    "export_river_today", "export_wave", "export_drivers", "export_flooded",
    "export_river_stage", "export_track", "export_csv", "export_floodwater",
    "export_area_stage", "export_exposure",
])
def test_a_failed_data_test_writes_nothing_at_all(tmp_path, name):
    """Every export, not just the ones somebody remembered.

    Parametrised over the whole surface on purpose: the danger is a new
    export added later that takes the ValidationResult and does not check
    it, which no test of a specific function would catch.
    """
    from parana_crecida import export as ex
    from parana_crecida.store import Store

    out = published(tmp_path)
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    thresholds = {}

    func = getattr(ex, name)
    with Store(":memory:") as store:
        store.load_gauges()
        # `result` is bound by keyword and the positional arguments are
        # filled from the signature, so this keeps working as the exports
        # change shape.
        import inspect

        args = []
        for pname, param in inspect.signature(func).parameters.items():
            if pname in ("result", "out_dir"):
                continue
            if param.kind is param.KEYWORD_ONLY:
                continue
            if param.default is not param.empty:
                continue
            args.append(store if pname == "store" else thresholds)
        try:
            func(*args, result=Failing(), out_dir=out)
        except ex.ExportBlocked:
            pass
        except Exception as error:  # pragma: no cover - diagnostic
            pytest.fail(f"{name} raised {error!r} instead of blocking")

    after = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert after == before, (
        f"{name} changed the published directory while the data tests were "
        f"failing; the last good export must stand"
    )


def test_the_export_gate_takes_a_validation_result_it_cannot_ignore():
    """"Export without validating" has to be unwriteable, not discouraged."""
    import inspect

    from parana_crecida import export as ex

    for name in dir(ex):
        if not name.startswith("export_"):
            continue
        signature = inspect.signature(getattr(ex, name))
        assert "result" in signature.parameters, (
            f"{name} does not take a ValidationResult, so it can be called "
            f"without one"
        )


def test_writes_are_atomic():
    """An interrupted run must leave the previous file, not half a new one."""
    import inspect

    from parana_crecida import export as ex

    source = inspect.getsource(ex.write_json)
    assert "replace" in source, (
        "write_json does not look atomic; an interrupted write would leave a "
        "truncated file where a complete one used to be"
    )


# -- code against exported data ------------------------------------------


def test_the_exported_parameters_are_the_ones_in_the_code():
    """A published file describing a different mask is worse than none."""
    path = SITE / "floodwater.json"
    if not path.exists():
        pytest.skip("nothing exported")
    payload = json.loads(path.read_text(encoding="utf-8"))
    from parana_crecida import floodwater as fw

    assert payload["vh_threshold_db"] == fw.VH_THRESHOLD_DB
    assert payload["hand_limit_m"] == fw.HAND_LIMIT_M
    assert payload["window_days"] == fw.WINDOW_DAYS
    if payload.get("vh_floor_db") is not None:
        assert payload["vh_floor_db"] == fw.VH_FLOOR_DB


def test_the_publication_floor_in_the_data_is_the_one_in_the_code():
    path = SITE / "floodwater.json"
    if not path.exists():
        pytest.skip("nothing exported")
    payload = json.loads(path.read_text(encoding="utf-8"))
    from parana_crecida.export import SKILL_FLOOR

    assert payload["skill_floor"] == SKILL_FLOOR


# -- code against documentation ------------------------------------------


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"-?\d+(?:\.\d+)?", text))


@pytest.mark.skipif(not DOCS.exists(), reason="no method document")
def test_the_method_document_states_the_parameters_in_use():
    """It is the statement of record, so it has to be true.

    Written because the project had two complete pipelines running and
    nothing said which produced the published figures.
    """
    from parana_crecida import floodwater as fw
    from parana_crecida.export import ANCHOR_CYCLES, MIN_CHANNEL_FRACTION, SKILL_FLOOR

    text = DOCS.read_text(encoding="utf-8")
    for label, value in (
        ("VH threshold", f"{abs(fw.VH_THRESHOLD_DB):g}"),
        ("VH floor", f"{abs(fw.VH_FLOOR_DB):g}"),
        ("HAND limit", f"{fw.HAND_LIMIT_M:g}"),
        ("window", f"{fw.WINDOW_DAYS:g}"),
        ("coverage floor", f"{fw.MIN_COVERAGE * 100:g}"),
        ("skill floor", f"{SKILL_FLOOR:g}"),
        ("channel fraction", f"{MIN_CHANNEL_FRACTION * 100:g}"),
        ("anchor cycles", f"{ANCHOR_CYCLES:g}"),
        ("cycles kept", f"{fw.KEEP_CYCLES:g}"),
    ):
        assert value in text, (
            f"docs/METHOD.md does not state the {label} ({value}) that the "
            f"code uses"
        )


@pytest.mark.skipif(not DOCS.exists(), reason="no method document")
def test_the_method_document_names_the_datasets_in_use():
    text = DOCS.read_text(encoding="utf-8")
    from parana_crecida import floodwater as fw

    for asset in (fw.S1, fw.HAND, fw.GSW_YEARLY, __import__('parana_crecida.flooded', fromlist=['GSW']).GSW):
        assert asset in text, f"docs/METHOD.md does not name {asset}"


@pytest.mark.skipif(not DOCS.exists(), reason="no method document")
def test_the_method_document_marks_what_no_longer_runs():
    """The table the audit found missing, held in place.

    Every artefact that was superseded, rejected, withdrawn or deleted has
    to stay labelled, or the document slowly becomes a description of a
    product that no longer exists.
    """
    text = DOCS.read_text(encoding="utf-8").lower()
    for state in ("superseded", "rejected", "withdrawn", "deleted", "withheld"):
        assert state in text, f"docs/METHOD.md never says {state}"
    # And the specific ones, by name.
    for artefact in ("animation.py", "tune_mask.py", "exposure.py",
                     "areastage.py", "flooded.water_mask"):
        assert artefact.lower() in text, f"docs/METHOD.md does not mention {artefact}"


@pytest.mark.skipif(not DOCS.exists(), reason="no method document")
def test_the_method_document_declares_the_validation_in_sample():
    """The largest open weakness, stated where a reader will meet it."""
    text = DOCS.read_text(encoding="utf-8").lower()
    assert "in-sample" in text
    assert "2026-10-22" in text


@pytest.mark.skipif(not README.exists(), reason="no README")
def test_the_readme_does_not_contradict_the_publishing_rule():
    """It listed the gorge as withheld for a week after it started
    publishing, in the summary a reader meets first."""
    path = SITE / "floodwater.json"
    if not path.exists():
        pytest.skip("nothing exported")
    payload = json.loads(path.read_text(encoding="utf-8"))
    published_keys = {s["key"] for s in payload.get("stretches", [])}
    text = README.read_text(encoding="utf-8")

    head = text[:text.index("## ")] if "## " in text else text
    if "gorge" in head.lower():
        gorge = {"puerto_iguazu_libertador", "libertador_posadas"}
        if gorge & published_keys:
            assert "does publish" in head or "used not to" in head, (
                "the README summary mentions the gorge while both gorge "
                "reaches publish; it used to list them as withheld"
            )


@pytest.mark.skipif(not METHOD_PAGE.exists(), reason="no method page")
def test_the_method_page_quotes_the_threshold_the_code_uses():
    from parana_crecida import floodwater as fw

    text = METHOD_PAGE.read_text(encoding="utf-8")
    # Written with a minus sign on the page, a hyphen in the source.
    wanted = f"{abs(fw.VH_THRESHOLD_DB):g}"
    assert f"−{wanted}" in text or f"-{wanted}" in text, (
        f"the method page does not quote the VH threshold in use ({wanted} dB)"
    )
    assert f"{fw.HAND_LIMIT_M:g} m" in text


@pytest.mark.skipif(not METHOD_PAGE.exists(), reason="no method page")
def test_no_page_still_claims_the_coverage_filters_are_unnecessary():
    """They were removed, the page said they were not needed, and the
    property it asserted instead was the one they used to verify."""
    text = METHOD_PAGE.read_text(encoding="utf-8").lower()
    for claim in ("neither is needed now", "ya no hacen falta",
                  "cover the whole corridor every time"):
        assert claim not in text, f"the method page still claims: {claim}"
