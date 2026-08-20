# Contributing to Villain

Thanks for taking an interest. Bug reports, new site parsers, and fixes are all
welcome. This file covers the dev setup, the one invariant the project will not
bend on, how the tests are laid out, and how to add support for a new site.

## Dev setup

Needs Python 3.11 or newer.

```bash
git clone https://github.com/aroraarnav/villain.git
cd villain

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e . pytest

pytest                             # should be green before you start
```

That installs the `villain` and `villain-ui` commands in editable mode, so your
changes are picked up without reinstalling. CI runs the same suite on Python
3.11, 3.12 and 3.13, so if it passes locally on one of those it will almost
certainly pass on the rest.

## The one rule

**No figure reaches the screen that the arithmetic did not produce.** Everything
the tool shows is derived from the stored hands and is reproducible from them;
nothing is guessed, rounded into existence, or borrowed from a different
statistic. A corollary is enforced in the tests: **every statistic that reaches
the user carries a glossary entry** describing what it counts and what *high* and
*low* mean, and a test fails if one appears without it (see `villain/glossary.py`
and `tests/`). If you add a number, add its glossary entry in the same change.

Constants that are judgment calls rather than derivations (for example
`CAPTURE` in `exploits.py` or `ADJUSTMENT_PRIOR` in `dynamics.py`) live in the
source with a comment saying so. Keep that habit: state the assumption where it
lives rather than burying it.

## Tests

The suite lives in `tests/` and is run with plain `pytest` (config is in
`pyproject.toml` under `[tool.pytest.ini_options]`).

- **Balance checks.** The parser tests assert every hand balances to the cent —
  chips in equal chips out. This is what proves the opcode decoding is correct,
  so a parser change that breaks the balance is a parser bug, not a flaky test.
- **Regressions are named for their bug.** Several tests in `test_profiling.py`
  and elsewhere are named after the modeling mistake that produced them. When
  you fix a wrong number, add a test named for what was wrong, so it cannot come
  back quietly.
- **Fixtures are anonymized.** `tests/data/pokernow_sample.json` uses generic
  `player1`…`player5` names and synthetic account ids. Any fixture you add must
  be the same — never commit a real export with real screen names.

## Player anonymity

This tool exists to say true, sometimes unflattering things about how specific
people play — that only stays defensible if the humans behind it never surface
in anything that leaves the local database. A real screen name has shown up
before as a "realistic" example in a docstring or comment explaining the
identity-matching code, and separately in a PR description quoting a real
read. Neither is fine: **no real screen name, real full name, or anything that
identifies a specific player belongs in code, comments, docstrings, tests,
commit messages, or PR descriptions.** The local database, `HANDOFF.md`, and
your own working notes are the only places that should ever see one — both are
gitignored for exactly this reason.

Need an example name for a comment or test? Use a fictional placeholder
(`player1`, `PlayerA`, `Ghost`, whatever reads clearly) rather than something
lifted from a real session. If a fictional pair needs to interact with the
matching algorithm in a specific way (share a suffix, drop the same vowels,
collide after normalizing), pick strings that actually reproduce that
behavior — check with `villain.identity.name_similarity` before trusting the
example, the same way you'd check any other figure that reaches a docstring.

Please add or update tests with any behavior change, and keep the suite green.

## Adding a parser for a new site

PokerNow is currently the only supported format, but the registry is built so a
new site touches nothing downstream. A parser is **any callable that takes a
`Path` and yields `Hand` objects** (`villain/parsers/base.py`):

```python
Parser  = Callable[[Path], Iterator[Hand]]
Sniffer = Callable[[Path], bool]
```

To add one:

1. Create `villain/parsers/<site>.py`. Write a `sniff(path) -> bool` that
   recognizes the format by content (not by filename), and a parser that yields
   canonical `Hand` objects from `villain/model.py`.
2. Register it at import time with
   `register("<site>", sniff, parse)` — see how `pokernow.py` does it.
3. Import your module in `villain/parsers/__init__.py` so it registers itself
   (the existing `from . import pokernow` line is the pattern).
4. Add a small anonymized sample under `tests/data/` and a test that parses it
   and asserts the hands balance to the cent.

`villain import` and the web UI both go through the registry's content sniffing,
so once your parser is registered and recognizes its files, everything
downstream — stats, profiles, the UI — works with no further changes.

## Style

- Type hints on public functions; the codebase uses `from __future__ import
  annotations`.
- Docstrings explain **why**, not what — the trade-off or the failure the code
  guards against, not a paraphrase of the code.
- Determinism: the same hands must always produce the same read. The only
  non-deterministic path is the optional LLM exploit suggestions, and those are
  clearly labeled as suggestions and checked against the arithmetic.

## Secrets

Never commit credentials. LLM settings are read from the environment, falling
back to `~/.villain/env` — deliberately outside the working tree so a key can
never be caught by a stray `git add -A`. The `.gitignore` already excludes the
common offenders (`*.env`, `.env`, `secrets*`).

## Pull requests

Work on a topic branch and open a PR against `main`. Keep the subject a plain
statement of what changed and why. CI must be green (pytest on 3.11–3.13) before
a merge.
