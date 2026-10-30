"""The published pages.

Nothing checked the pages until now, which is where every user-facing claim
actually lives. These are cheap structural checks against the class of bug
this project keeps meeting: something looks entirely healthy and is missing
or wrong.

What is asserted is what a reader would notice - a dead link in the nav, a
string that falls back to English because nobody translated it, a page that
fetches a file the export never writes, a view that quietly dropped the
"this is not an alert system" line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SITE = Path("site")
PAGES = sorted(SITE.glob("*.html"))
#: The nav. Four entries, because the project is a satellite water product
#: and not a gauge dashboard, and six entries said otherwise.
VIEWS = {"index.html", "map.html", "gauges.html", "method.html"}

#: Pages below the nav, and the nav entry each one lights up. They are
#: reachable from their section rather than from the nav, and a test below
#: holds them to that - an orphan page is worse than a long nav.
SECTION = {
    "flood.html": "index.html",
    "wave.html": "gauges.html",
    "drivers.html": "gauges.html",
}

pytestmark = pytest.mark.skipif(not PAGES, reason="no site to check")


def scan(text: str, start: int) -> int:
    """Index just past the object literal opening at ``start``.

    A brace counter that knows about quotes, because the pages hold their
    translations in a JavaScript object and the strings in it are full of
    punctuation. Template literals are counted through: the braces of a
    ``${...}`` balance each other.
    """
    depth = 0
    quote = None
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            elif quote == "`" and char == "{":
                depth += 1
            elif quote == "`" and char == "}":
                depth -= 1
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise AssertionError("unbalanced braces")


def keys_at_depth_one(block: str) -> set[str]:
    """The object's own keys, ignoring anything nested inside it."""
    found: set[str] = set()
    depth = 0
    quote = None
    index = 0
    while index < len(block):
        char = block[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            elif quote == "`" and char == "{":
                depth += 1
            elif quote == "`" and char == "}":
                depth -= 1
            index += 1
            continue
        if char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 1:
            match = re.match(r"([A-Za-z_$][\w$]*)\s*:", block[index:])
            if match:
                found.add(match.group(1))
                index += match.end()
                continue
        index += 1
    return found


def translations(page: Path) -> dict[str, set[str]]:
    """``{"es": {...keys...}, "en": {...}}`` for one page."""
    text = page.read_text(encoding="utf-8")
    start = text.index("const T = {") + len("const T = ")
    table = text[start:scan(text, start)]
    out: dict[str, set[str]] = {}
    for lang in ("es", "en"):
        marker = re.search(rf"\b{lang}\s*:\s*{{", table)
        assert marker, f"{page.name} has no {lang} translations"
        begin = marker.end() - 1
        out[lang] = keys_at_depth_one(table[begin:scan(table, begin)])
    return out


# -- the nav ---------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_links_to_every_view(page):
    nav = re.search(r"<nav class=\"views\".*?</nav>", page.read_text(encoding="utf-8"), re.S)
    assert nav, f"{page.name} has no view nav"
    linked = set(re.findall(r'href="([^"]+)"', nav.group(0)))
    assert linked == VIEWS, f"{page.name} nav: {sorted(linked)}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_nav_target_exists(page):
    nav = re.search(r"<nav class=\"views\".*?</nav>", page.read_text(encoding="utf-8"), re.S)
    for href in re.findall(r'href="([^"]+)"', nav.group(0)):
        assert (SITE / href).exists(), f"{page.name} links to a missing {href}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_each_page_marks_its_section_as_current(page):
    """A page off the nav lights up the entry it belongs under."""
    nav = re.search(r"<nav class=\"views\".*?</nav>", page.read_text(encoding="utf-8"), re.S)
    current = re.findall(r'href="([^"]+)"[^>]*aria-current="page"', nav.group(0))
    want = SECTION.get(page.name, page.name)
    assert current == [want], f"{page.name} marks {current}, wanted {want}"


@pytest.mark.parametrize("name", sorted(SECTION), ids=lambda n: n)
def test_every_page_below_the_nav_is_linked_from_its_section(name):
    """Taking a page out of the nav must not orphan it."""
    parent = SITE / SECTION[name]
    assert parent.exists(), f"{name} hangs off a missing {parent.name}"
    body = parent.read_text(encoding="utf-8")
    outside = re.sub(r"<nav class=\"views\".*?</nav>", "", body, flags=re.S)
    assert f'href="{name}"' in outside, (
        f"{name} is off the nav and {parent.name} does not link to it"
    )


# -- both languages, always ------------------------------------------------


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_spanish_and_english_carry_the_same_keys(page):
    """A key in one language only falls back to blank or to the other one."""
    table = translations(page)
    missing_en = table["es"] - table["en"]
    missing_es = table["en"] - table["es"]
    assert not missing_en, f"{page.name}: no English for {sorted(missing_en)}"
    assert not missing_es, f"{page.name}: no Spanish for {sorted(missing_es)}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_translated_slot_has_a_translation(page):
    """A ``data-t`` with no entry renders as an empty element."""
    text = page.read_text(encoding="utf-8")
    table = translations(page)
    used = set(re.findall(r'data-t="([^"]+)"', text))
    used |= set(re.findall(r'data-t-aria="([^"]+)"', text))
    for lang in ("es", "en"):
        missing = used - table[lang]
        assert not missing, f"{page.name} [{lang}]: nothing for {sorted(missing)}"


# -- the standing promise --------------------------------------------------


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_says_it_is_not_an_alert_system(page):
    """It goes on every page, and every page links to the official alert."""
    text = page.read_text(encoding="utf-8")
    assert "alerta.ina.gob.ar" in text, f"{page.name} does not link INA's alert"
    table = translations(page)
    assert "noticeStrong" in table["es"], f"{page.name} has no notice"
    assert "officialLink" in table["es"], f"{page.name} has no official link"


# -- what the pages ask for ------------------------------------------------


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_fetched_file_is_published(page):
    """A page fetching a file the export never writes renders blank.

    This is the failure that put the map live without its gauge and
    departamento layers: they had been written to the repository's data/
    directory instead of the site's, and nothing noticed.
    """
    text = page.read_text(encoding="utf-8")
    for target in re.findall(r'fetch\("([^"]+)"', text):
        if target.startswith("http"):
            continue
        assert (SITE / target).exists(), f"{page.name} fetches a missing {target}"


def test_the_flood_page_does_not_take_whichever_stretch_is_first():
    """Position zero is not a stable choice.

    The corridors are ordered upstream first. When the run was extended
    north to the Brazil frontier, stretches[0] silently became a gorge
    reach that publishes no area, and this page - whose loop and prose are
    about Corrientes to Goya - started showing it.
    """
    text = (SITE / "flood.html").read_text(encoding="utf-8")
    assert "(data.stretches || [])[0]" not in text
    assert 'corrientes_goya' in text


def test_the_flood_page_states_the_calibration_from_the_data():
    """Not from numbers typed into the prose, which drift when it changes."""
    text = (SITE / "flood.html").read_text(encoding="utf-8")
    assert "536" not in text and "521" not in text


def test_the_method_page_builds_its_tables_from_published_data():
    """Its validation table must not be numbers somebody typed.

    A method page that states a score from its own source is a claim that
    drifts the moment the measurement changes, and this table is the
    evidence for every figure the site publishes.
    """
    text = (SITE / "method.html").read_text(encoding="utf-8")
    for name in ("floodwater", "wave"):
        assert f'fetch("data/{name}.json")' in text, f"{name} is not fetched"
    assert "surface.skill" in text, "the table is not built from the skill table"
    # The scores themselves must not be written into the page.
    for typed in ("0.83", "0.90", "0.48", "536.3", "520.8"):
        assert f">{typed}<" not in text, f"{typed} is hardcoded in the markup"


# -- accessibility ---------------------------------------------------------


def test_the_stylesheet_respects_a_request_for_less_motion():
    """A reader who has asked their system for less motion gets less."""
    css = (SITE / "style.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css


def test_the_looping_animation_can_be_stopped():
    """A GIF runs for well over five seconds and cannot be paused.

    WCAG 2.2.2 wants a control for that, and a motion preference wants it to
    start stopped. Both are the page's job, because the image format offers
    neither.
    """
    text = (SITE / "index.html").read_text(encoding="utf-8")
    assert 'id="loopToggle"' in text
    assert "prefers-reduced-motion" in text
    for lang_key in ("pauseLoop", "playLoop"):
        assert text.count(lang_key) >= 3, lang_key


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_page_clears_an_svg_by_counting_nodes(page):
    """The bug that deleted two charts' descriptions.

    `childNodes` counts the whitespace between tags, so a chart whose nodes
    are [text, title, text, desc, text] loses its <desc> the moment it is
    redrawn - and on one page that <desc> was the target of the chart's own
    aria-labelledby.
    """
    text = page.read_text(encoding="utf-8")
    assert "childNodes.length >" not in text, (
        f"{page.name} trims an element by node count, which counts whitespace"
    )


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_offers_a_way_past_the_nav(page):
    """WCAG 2.4.1, and it is Level A.

    Six view links and two language buttons sit before the content on every
    page. Without a skip link that is eight stops before a keyboard user
    reaches what they came for, and nothing to skip to.
    """
    text = page.read_text(encoding="utf-8")
    body = text.index("<body>")
    skip = text.index('class="skip"')
    assert skip - body < 40, f"{page.name}: the skip link is not the first thing"
    # The target of the skip link, matched on the two attributes that make
    # it work rather than on an exact tag string, which broke the moment a
    # class was added for layout.
    main = re.search(r"<main[ >][^>]*>?", text)
    assert main, f"{page.name}: no <main>"
    assert 'id="content"' in main.group(0), page.name
    assert 'tabindex="-1"' in main.group(0), page.name
    assert text.index("<main") > text.index("</nav>"), page.name
    assert text.index("</main>") < text.index("<footer"), page.name


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_a_failed_load_is_announced(page):
    """An error box that appears silently tells a screen reader nothing."""
    text = page.read_text(encoding="utf-8")
    if 'id="error"' not in text:
        pytest.skip("no error box on this page")
    assert 'id="error" class="err" role="alert"' in text, page.name


def test_the_slider_announces_a_date_not_an_index():
    """A native range announces its value, which here is an array index.

    Without aria-valuetext a screen reader says "Fecha, 179" for the map's
    main control instead of the date it stands for.
    """
    text = (SITE / "map.html").read_text(encoding="utf-8")
    assert text.count('setAttribute("aria-valuetext"') >= 2, (
        "both the daily layer and the radar layer must set it"
    )


def test_the_csv_files_the_method_page_links_are_published():
    """A link to data nobody can download is not an open dataset."""
    text = (SITE / "method.html").read_text(encoding="utf-8")
    import re

    linked = re.findall(r'href="(data/csv/[^"]+)"', text)
    assert linked, "the method page links no CSV"
    for target in linked:
        assert (SITE / target).exists(), f"linked but missing: {target}"


def test_the_published_csv_withholds_what_the_json_withholds():
    """Exactly the same stretches, from a different code path.

    A CSV is the one place a withheld measurement could quietly come back,
    because it is written separately from the pages and because a CSV is
    the copy people download and keep.

    This used to name the three stretches by hand, which encoded a result
    rather than the rule behind it: when the mask was revalidated the two
    gorge reaches turned out to be the best on the river, and a test that
    said so by name would have had to be argued with rather than simply
    re-run.
    """
    import csv

    path = SITE / "data/csv/flooded_area.csv"
    published = SITE / "data/floodwater.json"
    if not path.exists() or not published.exists():
        pytest.skip("no CSV exported yet")

    payload = json.loads(published.read_text(encoding="utf-8"))
    allowed = {s["key"] for s in payload["stretches"]}
    withheld = {s["key"] for s in payload.get("withheld", [])}

    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    keys = {row["stretch"] for row in rows}

    assert keys <= allowed, f"the CSV publishes {sorted(keys - allowed)}"
    assert not (keys & withheld), f"withheld stretches in the CSV: {sorted(keys & withheld)}"
    # And the evidence travels with the figures, or the file is a set of
    # numbers with no way to tell how much to trust any of them.
    for row in rows:
        assert row["iou"], f"{row['stretch']} has no score in the CSV"


# -- the pipeline behind the pages -----------------------------------------


def test_the_daily_run_refreshes_what_the_front_page_reads():
    """Every file a page fetches must have something that rebuilds it.

    The front page was shipped reading data/floodwater.json, which at the
    time was produced by an ad-hoc script and by nothing in CI. The
    headline figure would have frozen on the day it was committed while
    the gauge pages went on updating three times a day, and the freshness
    gate pointed at that same file would have failed every run from ten
    days later.
    """
    workflow = Path(".github/workflows/daily.yml")
    if not workflow.exists():
        pytest.skip("no daily workflow")
    run = workflow.read_text(encoding="utf-8")

    for command in ("crecida floodwater", "crecida flooded", "crecida rainfall"):
        assert command in run, f"{command} is not in the daily run"

    # And what it measures has to be committed, or the run throws it away.
    for produced in ("data/floodwater.json", "site/data"):
        assert produced in run, f"{produced} is never committed"
