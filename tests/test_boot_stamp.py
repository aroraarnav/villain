"""A deploy has to change every URL the browser has cached, or the new UI
never arrives. The session lives in localStorage and is not part of this.

These used to assert that particular literal strings appeared in the boot page
-- which passed for as long as nobody reformatted the line, and said nothing at
all about an asset added later. They now derive the list of referenced files
from the sources, so a new script or face that forgets its stamp fails here
rather than on the hosted page a week later.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
BOOT = WEB / "index.html"
SHELL = WEB / "app-shell.js"
WORKER = WEB / "worker.js"
BUILD = WEB / "build.py"

#: Fetched deliberately unstamped, because it is what *carries* the stamp: the
#: page reads it to find out whether the copy of itself the browser cached is
#: the current deploy. It bypasses the HTTP cache by `cache: "no-store"`
#: instead, which is asserted below.
UNSTAMPED = {"manifest.json"}

#: Local files the browser is asked for by name. Anything matching this that is
#: not in UNSTAMPED has to carry a stamp.
ASSET = re.compile(r'["\'(]([a-z0-9][a-z0-9-]*\.(?:json|js|css|woff2|png))'
                   r'(?![a-z0-9])((?:\?v=)?)', re.I)


def _build():
    spec = importlib.util.spec_from_file_location("villain_web_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _referenced(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in ASSET.finditer(text)]


@pytest.mark.parametrize("source", [BOOT, SHELL], ids=lambda p: p.name)
def test_every_local_asset_url_is_cache_busted(source):
    for name, query in _referenced(source.read_text()):
        if name in UNSTAMPED:
            continue
        assert query == "?v=", (
            f"{source.name} asks for {name} with no ?v= stamp -- a deploy would "
            f"keep serving the browser's cached copy of it")


def test_the_manifest_is_the_one_thing_read_past_the_cache():
    """It is what the stamp is compared against, so a cached copy of it would
    report the deploy the browser already has and never trigger the reload."""
    shell = SHELL.read_text()
    assert 'fetch("manifest.json", { cache: "no-store" })' in shell
    assert "location.replace" in shell


@pytest.mark.parametrize("name", ["index.html", "app-shell.js"])
def test_stamping_rewrites_every_placeholder(name):
    build = _build()
    assert name in build.STAMPED
    stamped = build.stamp_boot_page((WEB / name).read_text(), "1710000000")
    assert "BUILD_STAMP" not in stamped
    assert "1710000000" in stamped


def test_stamping_refuses_a_file_with_no_placeholder():
    build = _build()
    with pytest.raises(ValueError, match="BUILD_STAMP"):
        build.stamp_boot_page("<html></html>", "1")


def test_the_deploy_id_reaches_the_worker_and_the_shell_stylesheet():
    """Both are built by the script rather than written in the page, so they
    are the two URLs a stamping bug can miss without the page looking wrong."""
    shell = SHELL.read_text()
    assert 'new Worker("worker.js?v=" + deployStamp)' in shell
    assert '"app-shell.css?v=" + deployStamp' in shell


def test_every_file_the_page_asks_for_is_one_the_build_produces():
    """A reference to a file the build never copies is a 404 that only shows up
    on the hosted deploy, because the source tree serves web/ directly and has
    the file sitting right there."""
    build = _build()
    copied = set(build.STAMPED) | set(build.COPIED)
    generated = {"manifest.json", "villain.db"}
    for source in (BOOT, SHELL):
        for name, _ in _referenced(source.read_text()):
            if name in generated or name.endswith(".woff2"):
                continue          # the faces are globbed out of the wheel's assets
            assert name in copied, (
                f"{source.name} asks for {name}, which web/build.py never puts "
                f"in dist/")


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


def test_the_third_party_script_is_pinned_and_checked():
    """It handles sign-in, so it is the one script on the page where "whatever
    the CDN resolves this tag to today" is not an acceptable answer.

    A floating major-version tag means a bad publish upstream reaches every
    visitor with a live session and nothing on this page notices. An exact
    version plus subresource integrity makes the browser refuse anything that
    is not the reviewed bytes; crossorigin is what makes the check apply.
    """
    html = BOOT.read_text()
    tags = re.findall(r'<script[^>]*src="(https://[^"]+)"[^>]*>', html, re.S)
    assert tags, "the boot page should still load the auth client"
    for src in tags:
        tag = re.search(r'<script[^>]*src="' + re.escape(src) + r'"[^>]*>', html, re.S).group(0)
        assert re.search(r"@\d+\.\d+\.\d+", src), (
            f"{src} is not pinned to an exact version")
        assert 'integrity="sha384-' in tag, f"{src} has no integrity hash"
        assert 'crossorigin=' in tag, (
            f"{src} has an integrity hash the browser will ignore without "
            f"crossorigin")
