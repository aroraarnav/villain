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
from urllib.parse import urlparse

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

#: File extensions worth checking. Anything the browser fetches by name and
#: caches.
ASSET_SUFFIXES = (".json", ".js", ".css", ".woff2", ".png", ".ico", ".svg")

#: Every URL-ish token: a quoted string, or the inside of a CSS ``url()``.
#: Matching the *whole* reference rather than just the filename is what lets an
#: absolute URL be checked. The first version keyed on a filename preceded by a
#: quote, which silently skipped every ``https://<site>/og-image.png`` -- the
#: form the Open Graph tags use, and exactly as able to 404 on deploy as a
#: relative one.
REFERENCE = re.compile(r'["\'(]\s*([^"\'()\s]+)\s*["\')]')


def _build():
    spec = importlib.util.spec_from_file_location("villain_web_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _own_host(text: str) -> str | None:
    """The site's own hostname, from its canonical link rather than hardcoded."""
    found = re.search(r'rel="canonical"\s+href="([^"]+)"', text)
    return urlparse(found.group(1)).hostname if found else None


def _referenced(text: str, own_host: str | None = None) -> list[tuple[str, str]]:
    """``(filename, query)`` for every asset on this page the *we* have to ship.

    Skips anything pointing at another host -- the auth client comes from a CDN
    and is nobody's file to copy -- and treats an absolute URL back to our own
    domain exactly like a relative one, because the browser does."""
    out = []
    for match in REFERENCE.finditer(text):
        raw = match.group(1)
        parsed = urlparse(raw)
        if parsed.scheme in ("data", "mailto"):
            continue
        if parsed.hostname and parsed.hostname != own_host:
            continue                      # somebody else's CDN, not ours to ship
        path = parsed.path or ""
        if path.startswith(("/static/", "/api/")):
            # Answered by the Python handler inside the page, not fetched from
            # the static host: `/static/app.css` lives in the wheel. Nothing to
            # copy, and nothing to cache-bust -- it never reaches the network.
            continue
        name = path.rsplit("/", 1)[-1]
        if not name.lower().endswith(ASSET_SUFFIXES):
            continue
        out.append((name, "?v=" if parsed.query.startswith("v=") else ""))
    return out


@pytest.mark.parametrize("source", [BOOT, SHELL], ids=lambda p: p.name)
def test_every_local_asset_url_is_cache_busted(source):
    text = source.read_text()
    for name, query in _referenced(text, _own_host(BOOT.read_text())):
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
    own = _own_host(BOOT.read_text())
    for source in (BOOT, SHELL):
        for name, _ in _referenced(source.read_text(), own):
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
    the progress message the page divides for the bar."""
    src = WORKER.read_text()
    assert "pyodide.ffi.create_proxy" not in src
    assert "bridge.build_hero(report)" in src
    assert "done: Number(done)" in src
    assert "total: Number(total)" in src
    assert "phase: String(phase)" in src


def test_a_guest_flush_keeps_a_definitions_rebuild():
    """Guests have no account, but IndexedDB still has to receive a stamped
    database or the next visit rebuilds the sample from scratch."""
    shell = SHELL.read_text()
    assert "if (r.wrote)" in shell
    assert "persistWrite = async () => { await toDisk(false); }" in shell


def test_the_third_party_script_is_pinned_and_checked():
    """It handles sign-in, so it is the one script on the page where "whatever
    the CDN resolves this tag to today" is not an acceptable answer.

    A floating major-version tag means a bad publish upstream reaches every
    visitor with a live session and nothing on this page notices. An exact
    version plus subresource integrity makes the browser refuse anything that
    is not the reviewed bytes; crossorigin is what makes the check apply."""
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


def test_signing_out_tells_the_next_load_rather_than_leaving_it_to_guess():
    """Sign-out lands on the landing page, deterministically.

    The auth client clears its stored session asynchronously, so the load
    straight after signing out could still read the session it was in the
    middle of removing -- dropping you back into the app signed in, with no
    landing page and no guest sign-in bar, over a freshly reseeded sample
    database. A second reload showed the gate correctly, which is what made it
    look intermittent instead of like a race.

    Both halves have to agree on the marker, so this checks they are written in
    terms of the same constant rather than two matching string literals."""
    shell = SHELL.read_text()

    assert re.search(r'const SIGNED_OUT = "\w+";', shell), (
        "the marker should be one named constant, not a literal in two places")
    # Sign-out sets it on the URL it navigates to.
    assert "url.searchParams.set(SIGNED_OUT" in shell
    # The next boot reads it, and does not simply trust the session.
    assert "new URLSearchParams(location.search).has(SIGNED_OUT)" in shell
    assert "url.searchParams.delete(SIGNED_OUT)" in shell, (
        "the marker has to come back off the URL, or a later reload re-signs-out")
    assert shell.count("SIGNED_OUT") >= 4

    # `user` has to be reassignable for the marker to be able to override it.
    assert "let user = authOn ? await sync.me() : null;" in shell, (
        "a const here would make the signed-out override impossible")


def test_a_sent_link_offers_back_and_the_demo_instead():
    """After the mail goes out the form used to vanish, leaving only
    'View the demo' and no way back to fix the address."""
    html = BOOT.read_text()
    shell = SHELL.read_text()
    assert 'id="back"' in html
    assert "View the demo instead" in shell
    assert "showForm" in shell
    assert "favicon.svg?v=" in html


def test_work_does_not_wait_on_frames_in_a_hidden_tab():
    """requestAnimationFrame is frozen in a background tab. A yield that
    only listens for frames never resolves, so a Hero build or import
    started and then backgrounded sat there until you came back."""
    shell = SHELL.read_text()
    assert "letItPaint" in shell
    assert 'visibilityState === "hidden"' in shell
    assert 'type: "visibility"' in shell
    worker = WORKER.read_text()
    assert "pageHidden" in worker
    assert 'msg.type === "visibility"' in worker
    # The assembled asset, not a source file: /static/app.js is concatenated
    # from villain/webapp/assets/app/*.js, and what ships is what to assert on.
    from villain.webapp.assets import static
    app = static("app.js")[0].decode()
    assert "nextFrame" in app
    assert 'visibilityState === "hidden"' in app


def test_the_worker_does_not_spin_while_the_tab_is_hidden():
    """The 6ms busy-wait existed so the page could paint. In the background
    there is no paint, and spinning a core for the whole Hero fit is how a
    backgrounded tab cooked the laptop."""
    src = WORKER.read_text()
    assert "yieldForPaint" in src
    assert "if (pageHidden) return;" in src
    assert "Atomics.wait" in src
    assert src.count("while (Date.now() < spin)") <= 1  # fallback only, not per-call


def test_signing_out_puts_up_the_blocking_veil():
    """It is a request, then a reload, then a runtime start. With only the
    button greying out, the app looked like it had ignored the click."""
    shell = SHELL.read_text()
    out = shell[shell.index("button.textContent = \"Sign out\""):]
    out = out[:out.index("chip.append(")]
    assert "showBusy(" in out, "sign-out should raise the same veil the rest of the UI uses"
    assert out.index("showBusy(") < out.index("await sync.signOut()"), (
        "the veil has to go up before the waiting starts, not after")
