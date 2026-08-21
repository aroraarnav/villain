"""A deploy has to change every URL the browser has cached, or the new UI
never arrives. The session lives in localStorage and is not part of this."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOT = ROOT / "web" / "index.html"
WORKER = ROOT / "web" / "worker.js"
BUILD = ROOT / "web" / "build.py"


def _build():
    spec = importlib.util.spec_from_file_location("villain_web_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_boot_page_names_the_placeholder_everywhere_it_must():
    html = BOOT.read_text()
    assert 'const STAMP = "BUILD_STAMP"' in html
    assert 'src="config.js?v=BUILD_STAMP"' in html
    assert 'src="sync.js?v=BUILD_STAMP"' in html
    assert "space-grotesk.woff2?v=BUILD_STAMP" in html
    assert "jetbrains-mono.woff2?v=BUILD_STAMP" in html
    assert 'cache: "no-store"' in html
    assert "location.replace" in html


def test_stamping_rewrites_every_placeholder():
    build = _build()
    stamped = build.stamp_boot_page(BOOT.read_text(), "1710000000")
    assert "BUILD_STAMP" not in stamped
    assert "worker.js?v=" in stamped
    assert 'src="sync.js?v=1710000000"' in stamped
    assert 'const STAMP = "1710000000"' in stamped


def test_stamping_refuses_a_page_with_no_placeholder():
    build = _build()
    with pytest.raises(ValueError, match="BUILD_STAMP"):
        build.stamp_boot_page("<html></html>", "1")


def test_worker_bypasses_the_http_cache_for_the_wheel():
    src = WORKER.read_text()
    assert 'cache: "no-store"' in src
    assert 'searchParams.set("v", deployStamp)' in src
    assert "boot: ({ base, stamp })" in src


def test_hero_progress_callback_is_a_js_function():
    """create_proxy lives on the Python pyodide.ffi module, not the JS one.

    The worker used to call pyodide.ffi.create_proxy and die on the first
    Hero visit with "create_proxy is not a function". A JS function handed
    to Python is already a JsProxy; Number() is what keeps a PyProxy off
    the progress message the page divides for the bar.
    """
    src = WORKER.read_text()
    assert "pyodide.ffi.create_proxy" not in src
    assert "bridge.build_hero(report)" in src
    assert "done: Number(done)" in src
    assert "total: Number(total)" in src
    assert "phase: String(phase)" in src
