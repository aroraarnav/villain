# Contributing to Villain

Bug reports, fixes and new site parsers are all welcome.

## Setup

Needs Python 3.11+.

```bash
git clone https://github.com/aroraarnav/villain.git
cd villain
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -q && ruff check .    # both should pass before you start
```

CI runs the same two checks on Python 3.11, 3.12 and 3.13.

## The rules

1. **No invented numbers.** Everything on screen must come from the stored
   hands. Every stat shown to a user needs an entry in `villain/glossary.py`,
   and a test fails without one. Constants that are judgment calls, like
   `CAPTURE` in `exploits.py`, say so in a comment.
2. **No real player names.** Villain profiles real people. Their screen names
   must never appear in code, comments, tests, fixtures, commit messages or PR
   descriptions. Use placeholders like `player1` or `PlayerA`. If a placeholder
   has to trigger specific name matching, check it with
   `villain.identity.name_similarity`.
3. **Same hands, same read.** Output must be deterministic.
4. **Docstrings say why, not what.** Explain the trade-off or the bug being
   prevented.
5. **No credentials in the repo.** Keep them in `~/.villain/env`.

## Tests

Plain `pytest` in `tests/`. Add or update tests with any behavior change.

- Parser tests check that every hand balances to the cent: chips in equal chips
  out. If that breaks, it's a parser bug.
- When you fix a wrong number, name the test after what was wrong, so the bug
  cannot come back unnoticed.
- Fixtures must be anonymized, like `tests/data/pokernow_sample.json`.

## Adding a site

PokerNow is the only parser so far, but a new site needs no changes downstream.

1. Create `villain/parsers/<site>.py` with a `sniff(path) -> bool` that
   recognizes the format by its content, and a parser that yields `Hand`
   objects (`villain/model.py`).
2. Call `register("<site>", sniff, parse)` at import time, as `pokernow.py` does.
3. Import the module in `villain/parsers/__init__.py`.
4. Add an anonymized sample to `tests/data/` and a test that it balances.

## Pull requests

Branch off `main`, and make sure CI passes. In the description, say what changed
and why. Back it up with real numbers from a run you actually did. Mention
anything you deliberately left alone.
