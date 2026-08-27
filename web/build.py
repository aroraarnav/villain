#!/usr/bin/env python3
"""Assemble the one-click browser demo into ``web/dist/``.

This is orchestration, not logic: it builds the wheel with the ordinary
packaging tools, seeds the preloaded database through the *real* ``villain
import`` path so the demo cannot diverge from the tool, warms the hero cache
so the thread-less browser never has to build it, and copies the boot page in.
Everything it needs already exists elsewhere in the repo.

    python web/build.py            # -> web/dist/ ready to serve or deploy
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist"
PLACEHOLDER = "BUILD_STAMP"


#: Files carrying the placeholder, copied into dist/ with it filled in. The
#: boot page names its scripts and faces; the shell script holds the deploy id
#: it compares against the live manifest and stamps the worker and stylesheet
#: URLs it builds itself. One placeholder across all of them so a deploy cannot
#: stamp one and miss another.
STAMPED = ("index.html", "app-shell.js")

#: The stylesheet the boot page borrows its colors from.
APP_CSS = ROOT / "villain" / "webapp" / "assets" / "app.css"

#: Boot-page-only tokens: the gate button's hover and the step line under the
#: bar. Both exist only while the page is still the boot screen, so app.css has
#: no counterpart and the sync below leaves them alone.
BOOT_ONLY = {"--red-hover", "--ink-dim"}

#: Copied through untouched. They carry no placeholder because nothing in them
#: names another asset -- the page and the shell own every URL between them.
COPIED = ("config.js", "sync.js", "worker.js", "app-shell.css",
          "og-image.png", "favicon.svg", "robots.txt")


def stamp_boot_page(text: str, stamp: str) -> str:
    """Bake a deploy id into every cache-busted URL in a file that names one.

    Missing the placeholder would ship an uncache-busted worker -- the failure
    this exists to prevent -- so that is an error, not a silent no-op.
    """
    if PLACEHOLDER not in text:
        raise ValueError(f"file is missing {PLACEHOLDER}")
    return text.replace(PLACEHOLDER, stamp)


#: ``--name: light-dark(<light>, <dark>);`` or ``--name: <hex>;``. The boot
#: page paints before any theme has been resolved and app.css's dark values are
#: what it was designed against, so the dark half is what gets borrowed.
_TOKEN = re.compile(
    r"(--[a-z0-9-]+)\s*:\s*(?:light-dark\(\s*#[0-9a-fA-F]{3,8}\s*,\s*)?"
    r"(#[0-9a-fA-F]{3,8})\s*\)?\s*;")


def dark_palette(css: str) -> dict[str, str]:
    """Every hex token app.css's bare ``:root`` declares, dark side."""
    block = re.search(r":root\s*\{(.*?)\n\s*\}", css, re.S)
    if not block:
        raise ValueError("app.css has no :root block")
    return {name: value.lower() for name, value in _TOKEN.findall(block.group(1))}


def sync_boot_palette(page: str, css: str) -> str:
    """Rewrite the boot page's borrowed colors from app.css.

    The hosted page paints a sign-in screen and a progress bar before the wheel
    carrying app.css has finished downloading, so it declares the handful of
    colors it needs itself. A value that drifts does not fail anything or look
    wrong in review -- it makes the site visibly change theme the moment the
    runtime loads, which reads to a visitor as two products rather than one.
    A comment saying "change it in both places" was the whole enforcement;
    deriving it here means there is only one place.
    """
    palette = dark_palette(css)

    def swap(m):
        name, value = m.group(1), m.group(2)
        if name in BOOT_ONLY:
            return m.group(0)
        if name not in palette:
            raise ValueError(
                f"the boot page declares {name}, which app.css's :root does not -- "
                f"either it is misnamed or it belongs in BOOT_ONLY")
        return m.group(0).replace(value, palette[name])

    root = re.search(r":root\s*\{(.*?)\n\s*\}", page, re.S)
    if not root:
        raise ValueError("the boot page has no :root block")
    fixed = _TOKEN.sub(swap, root.group(1))
    return page[:root.start(1)] + fixed + page[root.end(1):]


def run(*args: str) -> None:
    print("+", " ".join(args))
    subprocess.run(args, check=True, cwd=ROOT)


#: Non-Python files the package loads by path at runtime. A missing
#: ``package-data`` line leaves them out of the wheel and nothing local
#: notices -- the source tree still has them, so the suite passes and
#: ``villain test`` works. It breaks in the browser, which installs the wheel
#: and has nowhere to show the traceback. Checked here rather than trusted.
WHEEL_MUST_CARRY = (
    "villain/webapp/assets/index.html",
    "villain/webapp/assets/app.css",
    # /static/app.js is assembled from these at request time, so it is the
    # parts that have to be in the wheel; a missing one is a UI that half
    # loads, in the browser only, with nowhere to show the traceback.
    "villain/webapp/assets/app/00-base.js",
    "villain/webapp/assets/app/90-shell.js",
    "villain/copy/glossary.toml",
    "villain/copy/playbook.toml",
)


def check_wheel_data(wheel: Path) -> None:
    """Fail the build rather than deploy a wheel the browser cannot run."""
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    missing = [name for name in WHEEL_MUST_CARRY if name not in names]
    if missing:
        raise SystemExit(
            f"{wheel.name} is missing {missing}.\n"
            "Add them to [tool.setuptools.package-data] in pyproject.toml -- "
            "without it the hosted app raises on import and the local one does not."
        )
    print(f"+ wheel carries {len(WHEEL_MUST_CARRY)} data files")


def main() -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # 1. The package as a wheel. It is pure Python and carries the UI assets
    #    via package-data, so it installs in the browser under micropip.
    #
    #    build/ first, and this is not housekeeping: setuptools copies the
    #    package into build/lib and never removes what is no longer there, so
    #    a module deleted since the last build is still sitting in it and
    #    still goes into the wheel. Deleting six modules and finding all six
    #    in the wheel afterwards is how this was found.
    stale = ROOT / "build"
    if stale.exists():
        shutil.rmtree(stale)
    run(sys.executable, "-m", "build", "--wheel", "--outdir", str(DIST))
    wheel = sorted(DIST.glob("villain-*.whl"))[-1]
    check_wheel_data(wheel)

    # 2. The preloaded database: all ten archetypes playing real hands through
    #    the practice-simulator engine, stored and profiled the ordinary way.
    #    Deterministic, and far meatier than the twenty-hand parser fixture.
    db = DIST / "villain.db"
    sys.path.insert(0, str(WEB))
    from gen_dataset import build_demo_db
    print("+ generating demo dataset (all archetypes)")
    print(" ", build_demo_db(db))

    # 3. Warm the hero cache to disk beside the db. The browser has no threads
    #    to build it on, and a warmed cache (even an "no hero in this sample"
    #    result) means the Hero tab answers instantly instead of trying.
    from villain.db import Store
    from villain.webapp.heroview import hero_payload
    try:
        with Store(db) as store:
            hero_payload(store)
    except Exception as exc:                 # a cold hero page is a wart, not a failure
        print(f"  (skipped hero warm: {exc})")
    hero_cache = db.with_name(db.name + ".hero-cache.json")

    # 4. The boot page, the sync client, the worker that runs the Python, the
    #    (possibly blank) sync config, and a manifest so the page never
    #    hardcodes a wheel filename.
    # Every URL the boot page and the worker fetch is stamped: GitHub Pages
    # does not let us set Cache-Control, the wheel filename does not change
    # between deploys, and a cached worker is the previous application.
    stamp = str(int(time.time()))
    css_text = APP_CSS.read_text()
    for name in STAMPED:
        text = stamp_boot_page((WEB / name).read_text(), stamp)
        if name == "index.html":
            text = sync_boot_palette(text, css_text)
        (DIST / name).write_text(text)
        print(f"+ stamped {name}")
    for name in COPIED:
        shutil.copy(WEB / name, DIST / name)

    # 5. The typefaces, at the root of dist/. Every other asset is pulled from
    #    inside the wheel through the fetch shim, but a CSS url() is resolved
    #    by the browser and never touches that shim, so the faces have to be
    #    real files on the static host. They go beside index.html because the
    #    stylesheet names them bare: injected into a <style>, its base is the
    #    document, so "space-grotesk.woff2" means "next to the page".
    assets = ROOT / "villain" / "webapp" / "assets"
    for face in sorted(assets.glob("*.woff2")):
        shutil.copy(face, DIST / face.name)
        print(f"+ font {face.name}")
    (DIST / "manifest.json").write_text(json.dumps({
        "stamp": stamp,
        "wheel": wheel.name,
        "db": db.name,
        "hero_cache": hero_cache.name if hero_cache.exists() else None,
    }, indent=2))

    print(f"\nDemo assembled in {DIST}")
    print("Serve locally with:  python web/serve.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
