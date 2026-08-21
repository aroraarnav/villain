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
import shutil
import subprocess
import sys
import time
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

#: Copied through untouched. They carry no placeholder because nothing in them
#: names another asset -- the page and the shell own every URL between them.
COPIED = ("config.js", "sync.js", "worker.js", "app-shell.css")


def stamp_boot_page(text: str, stamp: str) -> str:
    """Bake a deploy id into every cache-busted URL in a file that names one.

    Missing the placeholder would ship an uncache-busted worker -- the failure
    this exists to prevent -- so that is an error, not a silent no-op.
    """
    if PLACEHOLDER not in text:
        raise ValueError(f"file is missing {PLACEHOLDER}")
    return text.replace(PLACEHOLDER, stamp)


def run(*args: str) -> None:
    print("+", " ".join(args))
    subprocess.run(args, check=True, cwd=ROOT)


def main() -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # 1. The package as a wheel. It is pure Python and carries the UI assets
    #    via package-data, so it installs in the browser under micropip.
    run(sys.executable, "-m", "build", "--wheel", "--outdir", str(DIST))
    wheel = sorted(DIST.glob("villain-*.whl"))[-1]

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
    for name in STAMPED:
        (DIST / name).write_text(stamp_boot_page((WEB / name).read_text(), stamp))
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
