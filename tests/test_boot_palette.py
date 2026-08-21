"""The boot page's palette has to be app.css's palette, exactly.

The hosted page paints a sign-in screen and a progress bar before the wheel
carrying app.css has finished downloading, so it declares the handful of colors
it needs itself. A value that drifts from app.css does not fail anything, look
wrong in review, or break a build -- it makes the site visibly change theme the
moment the runtime finishes loading, which reads to a visitor as two products
rather than one.

A comment saying "change it in both places" was the whole enforcement. This is
that comment, in a form that fails.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_CSS = ROOT / "villain" / "webapp" / "assets" / "app.css"
BOOT = ROOT / "web" / "index.html"

#: Declared by the boot page under its own name because it has no counterpart
#: in app.css -- both belong to the boot screen, which app.css never sees.
BOOT_ONLY = {"--red-hover", "--ink-dim"}

TOKEN = re.compile(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", re.I)


def _root_tokens(text: str) -> dict[str, str]:
    """Hex custom properties from the first bare ``:root`` block.

    Bare on purpose: app.css declares dark on ``:root`` and light as an
    override, and dark is what the boot page paints.
    """
    block = re.search(r":root\s*\{(.*?)\n\s*\}", text, re.S)
    assert block, "no :root block found"
    return {name: value.lower() for name, value in TOKEN.findall(block.group(1))}


@pytest.fixture(scope="module")
def palettes():
    return _root_tokens(APP_CSS.read_text()), _root_tokens(BOOT.read_text())


def test_the_boot_page_borrows_at_least_the_core_of_the_palette(palettes):
    """A boot page that stopped naming tokens would pass every check below by
    having nothing left to check."""
    _, boot = palettes
    assert {"--bg", "--ink", "--red"} <= set(boot)


def test_every_borrowed_token_matches_app_css(palettes):
    app, boot = palettes
    for name, value in sorted(boot.items()):
        if name in BOOT_ONLY:
            continue
        assert name in app, (
            f"the boot page declares {name}, which app.css's :root does not -- "
            f"either it is misnamed or it belongs in BOOT_ONLY")
        assert value == app[name], (
            f"{name} is {value} on the boot page and {app[name]} in app.css; "
            f"the page would change color when the runtime finishes loading")


def test_the_boot_page_uses_its_tokens_rather_than_repeating_hexes(palettes):
    """Repeating a literal is how the two files drifted in the first place: a
    hex written five times is five places to miss."""
    _, boot = palettes
    body = BOOT.read_text()
    body = re.sub(r":root\s*\{.*?\n\s*\}", "", body, count=1, flags=re.S)
    stray = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", body)}
    assert not stray, (
        f"raw hex outside :root: {sorted(stray)} -- name it as a token instead")


def test_boot_only_tokens_really_have_no_counterpart(palettes):
    """Keeps the escape hatch honest: a token listed as boot-only that app.css
    later grows is a token nobody is checking any more."""
    app, boot = palettes
    for name in BOOT_ONLY:
        assert name not in app, (
            f"{name} now exists in app.css -- drop it from BOOT_ONLY so it is "
            f"checked like the rest")
        assert name in boot, f"{name} is listed as boot-only but is not declared"
