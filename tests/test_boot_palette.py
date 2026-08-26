"""The boot page's palette is derived from app.css, not kept level with it.

The hosted page paints a sign-in screen and a progress bar before the wheel
carrying app.css has finished downloading, so it declares the handful of colors
it needs itself. This used to be four tests asserting the two files agreed --
a convention with a failing check bolted on. ``web/build.py`` now rewrites
those values from app.css on every deploy, so what is worth testing is the
rewriter, not the agreement it guarantees.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_CSS = ROOT / "villain" / "webapp" / "assets" / "app.css"
BOOT = ROOT / "web" / "index.html"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("web_build", ROOT / "web" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dark_side_is_what_gets_borrowed(build):
    """app.css states both themes per token via ``light-dark()``. The boot
    screen has no theme resolved yet and was designed against dark, so the
    second argument is the one that travels."""
    palette = build.dark_palette(APP_CSS.read_text())
    assert palette["--bg"] == "#0b0c0e"
    assert {"--bg", "--ink", "--red"} <= set(palette)


def test_a_drifted_value_is_repaired(build):
    page = BOOT.read_text().replace("--bg: #0b0c0e;", "--bg: #123456;")
    fixed = build.sync_boot_palette(page, APP_CSS.read_text())
    assert "#123456" not in fixed
    assert "--bg: #0b0c0e;" in fixed


def test_boot_only_tokens_are_left_alone(build):
    """The gate's hover and the step line have no counterpart in app.css."""
    fixed = build.sync_boot_palette(BOOT.read_text(), APP_CSS.read_text())
    assert "--red-hover: #c9524a;" in fixed


def test_a_token_app_css_does_not_have_fails_the_build(build):
    """The escape hatch stays honest: a new borrowed token that is merely
    misspelled would otherwise ship as an unstyled boot screen."""
    page = BOOT.read_text().replace("--edge:", "--rim:")
    with pytest.raises(ValueError, match="--rim"):
        build.sync_boot_palette(page, APP_CSS.read_text())


def test_the_boot_page_uses_its_tokens_rather_than_repeating_hexes():
    """A hex written five times is five places the sync cannot reach."""
    import re
    body = re.sub(r":root\s*\{.*?\n\s*\}", "", BOOT.read_text(), count=1, flags=re.S)
    stray = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", body)}
    assert not stray, f"raw hex outside :root: {sorted(stray)} -- name it as a token"
