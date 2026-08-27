"""Parser registry and format sniffing.

A parser is any callable that takes a path and yields ``Hand`` objects. They
register themselves here so ``villain import`` can be pointed at a directory of
mixed exports and sort them out itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from ..model import Hand

Parser = Callable[[Path], Iterator[Hand]]
Sniffer = Callable[[Path], bool]

_REGISTRY: list[tuple[str, Sniffer, Parser]] = []


def register(site: str, sniff: Sniffer, parse: Parser) -> None:
    _REGISTRY.append((site, sniff, parse))


def detect(path: Path) -> str | None:
    """Return the site name of the parser that claims this file, if any."""
    for site, sniff, _ in _REGISTRY:
        try:
            if sniff(path):
                return site
        except Exception:
            continue
    return None


def parse_file(path: Path) -> list[Hand]:
    path = Path(path)
    for _, sniff, parse in _REGISTRY:
        try:
            claimed = sniff(path)
        except Exception:
            continue
        if claimed:
            return list(parse(path))
    raise UnknownFormat(f"no parser recognizes {path.name}")


def parse_paths(paths: Iterable[Path]) -> Iterator[tuple[Path, list[Hand]]]:
    """Walk files and directories, yielding (path, hands) for what parses.

    Files that no parser claims are skipped silently -- an export directory is
    usually full of unrelated things."""
    for raw in paths:
        p = Path(raw)
        files = sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
        for f in files:
            try:
                hands = parse_file(f)
            except UnknownFormat:
                continue
            if hands:
                yield f, hands


class UnknownFormat(ValueError):
    pass
