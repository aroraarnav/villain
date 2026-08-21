"""Every data file the code reads has to reach the wheel.

The package ships two directories of non-Python files -- the UI's assets and
the interface's words -- and both are loaded by path, relative to their module.
Leave one out of ``package-data`` and nothing local notices: the source tree
still has the file, so the suite passes and ``villain test`` works. It fails
only in the browser, where the app is installed from the wheel and there is
nobody to read the traceback.

This asserts the declaration covers what is actually on disk. web/build.py
makes the same assertion against the built wheel, which is the stronger check;
this one is here so the mistake is caught before a build is even attempted.
"""

from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "villain"


def _declared() -> dict[str, list[str]]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        config = tomllib.load(fh)
    return config["tool"]["setuptools"]["package-data"]


def _data_files() -> list[Path]:
    """Everything under villain/ that is not Python source."""
    return sorted(
        p for p in PACKAGE.rglob("*")
        if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
    )


def test_every_data_file_is_declared():
    declared = _declared()
    missing = []
    for path in _data_files():
        relative = path.relative_to(ROOT)
        covered = False
        for package, patterns in declared.items():
            base = ROOT / Path(package.replace(".", "/"))
            if base not in path.parents:
                continue
            inside = path.relative_to(base).as_posix()
            if any(fnmatch(inside, pattern) for pattern in patterns):
                covered = True
                break
        if not covered:
            missing.append(str(relative))
    assert not missing, (
        "these ship in the source tree but not in the wheel, so they exist "
        f"locally and are absent in the browser: {missing}"
    )


def test_the_files_the_loaders_name_are_really_there():
    """The declaration is worth nothing if it points at a directory that moved."""
    from villain.glossary import COPY as GLOSSARY
    from villain.playbook import COPY as PLAYBOOK
    from villain.webapp.assets import ASSETS

    assert GLOSSARY.is_file(), GLOSSARY
    assert PLAYBOOK.is_file(), PLAYBOOK
    assert (ASSETS / "app.css").is_file()
