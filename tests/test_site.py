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

import re
from pathlib import Path

import pytest

SITE = Path("site")
PAGES = sorted(SITE.glob("*.html"))
VIEWS = {
    "index.html", "wave.html", "drivers.html",
    "flood.html", "map.html", "method.html",
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
def test_each_page_marks_itself_as_current(page):
    nav = re.search(r"<nav class=\"views\".*?</nav>", page.read_text(encoding="utf-8"), re.S)
    current = re.findall(r'href="([^"]+)"[^>]*aria-current="page"', nav.group(0))
    assert current == [page.name], f"{page.name} marks {current} as current"


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
    """Its calibration table must not be numbers somebody typed.

    A method page that states a threshold from its own source is a claim
    that drifts the moment the measurement changes.
    """
    text = (SITE / "method.html").read_text(encoding="utf-8")
    assert 'fetch("data/flooded.json")' in text
    assert 'fetch("data/wave.json")' in text
    assert "threshold_db" in text
    # The calibrated figures themselves must not be written into the page.
    assert "536.3" not in text and "520.8" not in text


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
    text = (SITE / "flood.html").read_text(encoding="utf-8")
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
