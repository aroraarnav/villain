"""Scaffolding the simulator tests build a villain out of.

Four files were driving :func:`villain.botplay.decide` and each carried its own
copy of these three -- a stub profile whose frequencies are whatever the test
names, and a bench of seats to deal them. The copies were identical, which is
the problem: ``opps`` defaults to 500 so a stubbed rate clears every sample bar
in the policy, and a file whose copy drifted off that number would have tested
the prior instead of the frequency, silently and only in that file.
"""

from dataclasses import dataclass

from villain.holdem import Seat


@dataclass
class Est:
    """One measured frequency. ``opps`` is past every bar botplay checks."""
    value: float
    opps: float = 500.0


class Prof:
    """A profile that measures exactly the frequencies named, and nothing else."""

    def __init__(self, **freqs):
        self.stats = {k: Est(v) for k, v in freqs.items()}


def seats(*stacks):
    """Seats named A, B, C... with the given starting stacks."""
    return [Seat(chr(65 + i), s) for i, s in enumerate(stacks)]
