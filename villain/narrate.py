"""Optional plain-English summary from a local language model.

Off unless configured. Talks to any OpenAI-compatible ``/chat/completions``
endpoint. Settings from the environment, then ``~/.villain/env`` (outside the
repo, so a key cannot be committed). Output is checked against the fact sheet
-- invented figures are discarded. Synthesis on top of the playbook, never a
replacement for it.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .glossary import stat_help

DEFAULT_URL = "http://localhost:11434/v1/chat/completions"
DEFAULT_MODEL = "llama3.2"

#: Lightest first. The job is short, specific bullets from a fact sheet,
#: not prose that needs a large model -- and the small models have the
#: roomier free quotas, which is what actually decides whether the button
#: works when you press it.

#: A 450-word report is a slow generation. The old 60s was tight enough that a
#: busy endpoint looked like a broken one.
TIMEOUT = 120

#: Hosted endpoints rate-limit and fall over briefly; a free tier does both
#: more often. These are the statuses worth trying again, and the ones that
#: never are: 401 will not fix itself, and neither will 404 on a retired model.
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
ATTEMPTS = 3
BACKOFF = 2.0
MAX_BACKOFF = 30.0


def _retry_after(exc) -> float | None:
    """The server's own Retry-After, in seconds, if it sent one."""
    try:
        value = exc.headers.get("Retry-After")
    except Exception:
        return None
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

#: Outside the project directory on purpose -- see the module docstring.
CONFIG_PATH = Path.home() / ".villain" / "env"

SETTINGS = ("VILLAIN_LLM_URL", "VILLAIN_LLM_MODEL", "VILLAIN_LLM_MODELS",
            "VILLAIN_LLM_KEY")


def _config() -> dict[str, str]:
    """Settings from ``~/.villain/env``. The environment always wins."""
    values: dict[str, str] = {}
    try:
        text = CONFIG_PATH.read_text()
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name in SETTINGS:
            values[name] = value.strip().strip("\'\"")
    return values


def setting(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _config().get(name) or default

SYSTEM = (
    "You are a poker coach. You are given facts computed from one opponent's "
    "hand history, including the leaks a rule engine has already reported. "
    "Your job is to find the exploits it missed.\n\n"
    "Write bullet points. Nothing else -- no introduction, no summary, no "
    "closing paragraph. Each bullet is one specific, playable exploit.\n\n"
    "Each bullet must give:\n"
    "- the exact situation it applies to (street, position, what happened "
    "before);\n"
    "- what to do, with a bet size where the facts support one;\n"
    "- why it works, in terms of what their numbers say about the range they "
    "hold there.\n\n"
    "Rules you must follow:\n"
    "- Go past the leaks already listed. Do not restate them. Combine "
    "statistics, look at what a frequency implies two streets later, and find "
    "the spots that follow from several numbers at once rather than one.\n"
    "- Use only the numbers given. Never state a figure that is not in the "
    "facts. Describe frequencies you were not given in words, or leave them "
    "out.\n"
    "- Be concrete. 'Bet more against them' is not an exploit; 'when they "
    "check the turn after calling a flop bet, bet two thirds pot with any two "
    "cards' is.\n"
    "- Four to seven bullets. Each one to three sentences.\n"
    "- If the sample is too thin to support a bullet, do not write it. Fewer "
    "real exploits beats a full list of guesses.\n"
    "- Plain English a player would use at the table."
)


@dataclass
class Narration:
    text: str
    model: str
    endpoint: str


class Unavailable(RuntimeError):
    """No model configured, or it could not be reached."""


def models() -> list[str]:
    """The models to try, best first.

    Free tiers meter each model separately, so a second name is worth more
    than a longer sleep: when one is out of quota another usually answers
    immediately. ``VILLAIN_LLM_MODELS`` is a comma-separated fallback chain.
    """
    listed = setting("VILLAIN_LLM_MODELS")
    if listed:
        return [name.strip() for name in listed.split(",") if name.strip()]
    return [setting("VILLAIN_LLM_MODEL", DEFAULT_MODEL)]


def enabled() -> bool:
    """True when a narrator has been explicitly configured."""
    return bool(setting("VILLAIN_LLM_MODEL") or setting("VILLAIN_LLM_MODELS")
                or setting("VILLAIN_LLM_URL"))


#: Numbers a sentence can contain without claiming anything about the player:
#: counting words rendered as digits, and the streets of a hand.
SAFE_NUMBERS = {"0", "1", "2", "3", "4", "5", "100"}

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_INJECTION = re.compile(
    r"(?i)(system\s*:|assistant\s*:|ignore (all |any )?previous|you are now)")


def _safe_label(name: str | None, fallback: str = "unknown") -> str:
    """Strip control characters and role-break attempts from a screen name.

    The fact sheet is the whole user message. A name containing a newline and
    ``SYSTEM:`` is otherwise free to rewrite the instructions.
    """
    text = re.sub(r"[\x00-\x1f\x7f]", "", name or "").replace("\n", " ").strip()
    if not text or _INJECTION.search(text):
        return fallback
    return text[:80]


def _percent_forms(value: str) -> set[str]:
    """Whole and rounded forms of a percentage figure."""
    out = {value}
    try:
        number = float(value)
    except ValueError:
        return out
    out.add(str(int(round(number))))
    out.add(f"{number:.0f}")
    if "." in value:
        out.add(value.split(".", 1)[0])
    return out


def unsupported_numbers(text: str, facts: str) -> list[str]:
    """Figures in ``text`` that do not appear in ``facts``.

    Percentages are matched as percentages: a fact sheet that says "Seen 78
    times" must not authorize "folds 78%". Bare counts still need to appear as
    bare numbers. Deliberately strict — invented frequencies read like real
    ones, so the only defense is refusing figures the arithmetic did not make.
    """
    known_pcts: set[str] = set()
    for value in _PERCENT.findall(facts):
        known_pcts |= _percent_forms(value)

    invented: list[str] = []
    for value in _PERCENT.findall(text):
        if _percent_forms(value).isdisjoint(known_pcts):
            invented.append(f"{value}%")

    known_bare = set(_NUMBER.findall(facts))
    for value in list(known_bare):
        if "." in value:
            known_bare.add(value.split(".", 1)[0])
            try:
                known_bare.add(str(round(float(value))))
            except ValueError:
                pass
    # Digits that were already judged as percentages are not re-checked bare.
    bare_text = _PERCENT.sub(" ", text)
    for value in _NUMBER.findall(bare_text):
        if value in known_bare or value in SAFE_NUMBERS:
            continue
        invented.append(value)
    return sorted(set(invented))


def describe_endpoint() -> str:
    """Where calls go, for showing the user without leaking the key."""
    url = setting("VILLAIN_LLM_URL", DEFAULT_URL)
    host = url.split("/")[2] if "//" in url else url
    return f"{models()[0]} at {host}"


def fact_sheet(payload: dict) -> str:
    """The only thing the model is allowed to know, built from the profile."""
    lines = [
        f"Player: {_safe_label(payload.get('name'))}",
        f"Table size: {payload.get('table_mix') or payload.get('regime_label') or payload.get('regime', '')}",
        f"Hands observed: {payload.get('hands', 0)} "
        f"({payload.get('sample_quality', 'unknown')})",
        f"Player type: {payload.get('archetype', 'unknown')} "
        f"({round(100 * payload.get('archetype_confidence', 0))}% confident)",
    ]
    summary = (payload.get("summary") or "").strip()
    if summary:
        lines.append(f"Type description: {summary}")
    lines.append(
        f"Skill rating: {payload.get('skill', {}).get('score', 0)} out of 100 "
        f"({payload.get('skill', {}).get('tier', 'unknown')})"
    )
    for item in payload.get("strengths") or []:
        lines.append(f"Does competently (do not attack): {item}")

    # Only well-observed frequencies, and each one spelled out. Handing over a
    # key like "cbet:turn" invites a confident sentence about the wrong thing:
    # the number is real, the reading is not, and no guard on invented figures
    # catches a correctly quoted statistic used to mean something else.
    measured = []
    for stat, entry in (payload.get("stats") or {}).items():
        if not isinstance(entry, dict) or (entry.get("opportunities") or 0) < 10:
            continue
        help_text = stat_help(stat)
        if help_text is None:
            # If the tool cannot say unambiguously what a number counts, it has
            # no business handing it to something that will write a confident
            # sentence about it.
            continue
        measured.append(f"- {help_text['what']} {round(100 * entry['value'])}%, "
                        f"measured over {round(entry['opportunities'])} spots")
    if measured:
        lines.append("Measured frequencies. Each line says exactly what was "
                     "counted; do not read them as anything else:")
        lines.extend(measured)

    if payload.get("leaks"):
        lines.append("ALREADY REPORTED to the user -- do not restate these, "
                     "build past them:")
        for leak in payload["leaks"]:
            lines.append(
                f"- {leak['headline']}. {leak['in_words']} "
                f"Worth about {leak['severity_bb100']} big blinds per 100 hands "
                f"({leak['size']}, {leak['tier']} read). "
                f"What they are doing: {leak['behavior']} "
                f"Do: {leak['do']} Do not: {leak['dont']}")
    else:
        lines.append("No leak has enough evidence behind it yet.")
    for combo in payload.get("combinations", []):
        lines.append(f"Also already reported: {combo['headline']}. {combo['body']}")
    for item in payload.get("watchlist") or []:
        lines.append(f"Suspected but unconfirmed: {item['headline']}. "
                     f"{item['in_words']}")
    return "\n".join(lines)


def narrate(payload: dict, *, url: str | None = None, model: str | None = None,
            timeout: int = TIMEOUT, attempts: int = ATTEMPTS) -> Narration:
    """Ask the configured model to summarize a profile.

    Retries the failures that are worth retrying: rate limits, gateway errors
    and timeouts, which a free tier produces regularly and which mean nothing
    about the request. Authentication and unknown-model errors are raised at
    once, because trying those again just adds delay to the same answer.

    A response that states figures the profile did not produce is also retried,
    once, with the offending numbers named. Models mostly comply when told
    exactly what they got wrong, and discarding a whole report over one invented
    percentage is a worse outcome than asking again.
    """
    # Defaulting the URL to local Ollama is right for the CLI, where that is
    # the documented setup. It is wrong for a press of the web button with
    # nothing configured: the hosted app has no loopback model, and the error
    # then tells you to start one. Refuse here, before the request is made.
    if url is None and model is None and not enabled():
        raise Unavailable(
            "No language model is configured. Set VILLAIN_LLM_URL or "
            "VILLAIN_LLM_MODEL in ~/.villain/env (see the README).")
    url = url or setting("VILLAIN_LLM_URL", DEFAULT_URL)
    chain = [model] if model else models()
    facts = fact_sheet(payload)

    headers = {"Content-Type": "application/json"}
    key = setting("VILLAIN_LLM_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    last: Exception | None = None
    correction = ""
    for index, model in enumerate(chain):
        exhausted = False
        wait = 0.0
        for attempt in range(max(1, attempts)):
            if attempt:
                time.sleep(wait or BACKOFF ** attempt)
            try:
                text = _one_call(url, model, headers, facts + correction, timeout)
            except _Retryable as exc:
                last = exc.__cause__ or exc
                wait = min(exc.wait or 0.0, MAX_BACKOFF)
                # A spent quota will not clear inside a backoff, but the next
                # model in the chain is metered separately and usually answers.
                if exc.out_of_quota:
                    exhausted = True
                    break
                continue

            invented = unsupported_numbers(text, facts)
            if not invented:
                return Narration(text=text, model=model, endpoint=url)
            # Name the offending figures and ask again. Discarding a whole
            # report over one invented percentage is the worse outcome.
            last = Unavailable("model stated figures that are not in the data "
                               f"({', '.join(invented)}); discarded")
            correction = (
                "\n\nIMPORTANT: a previous attempt was rejected for stating "
                f"these numbers, which are not in the facts above: "
                f"{', '.join(invented)}. Use only figures given above, or "
                "describe them in words.")
        if exhausted and index + 1 < len(chain):
            continue
    raise last if last else Unavailable("no response")


def _one_call(url: str, model: str, headers: dict, facts: str, timeout: int) -> str:
    """A single request. Raises ``_Retryable`` for failures worth another go."""
    body = json.dumps({
        "model": model,
        "temperature": 0.3,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": facts}],
    }).encode()
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        # The endpoint answered and refused. Its own message is more useful
        # than anything guessed here.
        detail = ""
        try:
            payload = json.load(exc)
            if isinstance(payload, list) and payload:
                payload = payload[0]
            detail = (payload or {}).get("error", {}).get("message", "")
        except Exception:
            pass
        failure = Unavailable(
            f"{model} returned {exc.code}" + (f": {detail}" if detail else f" ({exc.reason})"))
        if exc.code in RETRY_STATUS:
            # A spent quota is not a busy server: waiting will not fix it
            # before the window resets, but another model usually answers.
            spent = exc.code == 429 and "quota" in detail.lower()
            raise _Retryable(_retry_after(exc), out_of_quota=spent) from failure
        raise failure from exc
    except urllib.error.URLError as exc:
        # Nothing answered at all. Only suggest starting a local model when the
        # endpoint actually is local; telling somebody to "ollama pull" a
        # hosted model name is worse than saying nothing.
        local = "localhost" in url or "127.0.0.1" in url
        hint = (f"Start it with 'ollama serve' and 'ollama pull {model}'."
                if local else
                "Check the endpoint and your network, or set VILLAIN_LLM_URL "
                "to another OpenAI-compatible endpoint.")
        raise _Retryable() from Unavailable(
            f"could not reach {url}: {exc.reason}. {hint}")
    except (TimeoutError, OSError):
        raise _Retryable() from Unavailable(f"{model} did not respond in {timeout}s")

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise Unavailable(f"unexpected response from {url}") from exc


class _Retryable(Exception):
    """Internal marker: this failure is worth another attempt.

    ``wait`` carries the server's own ``Retry-After`` when it sent one. A free
    tier's limits are usually per minute, and guessing a two second backoff
    against a sixty second window just burns the retries.
    """

    def __init__(self, wait: float | None = None, out_of_quota: bool = False):
        super().__init__()
        self.wait = wait
        self.out_of_quota = out_of_quota
