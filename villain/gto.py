"""How close a player sits to a GTO / equilibrium baseline -- and one rating for it.

The rest of the tool measures a player against a *population* ("how do people
play"); this measures them against *optimal* ("how should they"), and the gap
is the part of their game a perfect opponent still could not exploit.

Two fidelities, never blurred, each tagged on every row it produces:

* **Preflop is a solver reference.** Opening, 3-betting, 4-betting and
  blind-defense frequencies at 100bb are stable, well-published solver outputs;
  the preflop targets here are that consensus for each table-size class. They
  are exact as *category frequencies* -- the by-position openers assume the
  standard seats for the class, and true GTO shifts a little with stack depth
  and bet sizing, which the UI says plainly.
* **Postflop is a benchmark.** Real postflop GTO is board-by-board and needs a
  solver in the loop, which this tool does not have. The postflop targets are
  equilibrium *benchmarks* -- board-averaged frequencies a balanced player
  lands near -- and every postflop row is labeled ``benchmark`` so the number
  is never mistaken for solver output.

Nothing here invents precision it does not have: a stat is only scored when the
player has enough of it, the rating weights the exact (preflop) rows above the
benchmark (postflop) ones, and the closeness curve is smooth rather than a hard
pass/fail on an arbitrary cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass

from .priors import FULL, HEADS_UP, SHORT, THREE

#: Solver-reference preflop (exact as category frequencies); everything else is
#: a board-averaged postflop benchmark.
EXACT = "solver"
BENCHMARK = "benchmark"

_PREFLOP = {
    "rfi", "rfi:UTG", "rfi:UTG1", "rfi:UTG2", "rfi:MP", "rfi:MP1", "rfi:MP2",
    "rfi:LJ", "rfi:HJ", "rfi:CO", "rfi:BTN", "rfi:SB",
    "three_bet", "fold_to_three_bet", "four_bet", "fold_to_four_bet",
    "bb_defend", "fold_to_steal", "steal", "three_bet_vs_steal",
}

#: Below this many opportunities a stat is not scored -- a GTO gap read off ten
#: hands is noise, same floor discipline the rest of the tool keeps.
MIN_OPPS = 20

#: The half-closeness point: a stat this far (in rate) from its target scores
#: 0.5. A smooth Lorentzian, so being a little off costs a little and there is
#: no cliff at an arbitrary threshold.
_TOL = 0.08

#: GTO / equilibrium targets by table-size class. Preflop = solver consensus at
#: 100bb; postflop = equilibrium benchmark. 6-max (SHORT) is written out; the
#: others adjust the openers and defense for seat count.
GTO: dict[str, dict[str, float]] = {
    SHORT: {
        # -- preflop openers, standard 6-max seats (solver, 100bb) --
        "rfi:UTG": 0.16, "rfi:MP": 0.17, "rfi:LJ": 0.18, "rfi:HJ": 0.21,
        "rfi:CO": 0.27, "rfi:BTN": 0.46, "rfi:SB": 0.42,
        # -- preflop facing actions (solver, 100bb) --
        "three_bet": 0.09, "fold_to_three_bet": 0.50,
        "four_bet": 0.10, "fold_to_four_bet": 0.45,
        "bb_defend": 0.58, "fold_to_steal": 0.48,
        "steal": 0.42, "three_bet_vs_steal": 0.15,
        # -- postflop (benchmark, board-averaged) --
        "cbet:flop": 0.55, "cbet:turn": 0.50, "cbet:river": 0.45,
        "fold_to_cbet:flop": 0.45, "fold_to_cbet:turn": 0.45, "fold_to_cbet:river": 0.48,
        "aggression:flop": 0.35, "aggression:turn": 0.33, "aggression:river": 0.30,
        "wwsf": 0.48, "wtsd": 0.28, "wsd": 0.53,
    },
    HEADS_UP: {
        # Only the blinds exist heads-up; the button is the small blind.
        "rfi:SB": 0.82, "rfi:BTN": 0.82,
        "three_bet": 0.17, "fold_to_three_bet": 0.45,
        "four_bet": 0.12, "fold_to_four_bet": 0.42,
        "bb_defend": 0.68, "fold_to_steal": 0.32,
        "steal": 0.82, "three_bet_vs_steal": 0.17,
        "cbet:flop": 0.62, "cbet:turn": 0.52, "cbet:river": 0.46,
        "fold_to_cbet:flop": 0.42, "fold_to_cbet:turn": 0.42, "fold_to_cbet:river": 0.45,
        "aggression:flop": 0.40, "aggression:turn": 0.36, "aggression:river": 0.32,
        "wwsf": 0.50, "wtsd": 0.33, "wsd": 0.52,
    },
}

# Three-handed: openers between heads-up and six-max, defense near heads-up.
GTO[THREE] = dict(
    GTO[SHORT],
    **{"rfi:CO": 0.34, "rfi:BTN": 0.50, "rfi:SB": 0.46},
    three_bet=0.13, fold_to_three_bet=0.47, bb_defend=0.62,
    fold_to_steal=0.44, steal=0.50, three_bet_vs_steal=0.16,
    aggression__pf=0.0,
)
GTO[THREE].pop("aggression__pf", None)

# Full ring: tighter opens up front, tighter overall.
GTO[FULL] = dict(
    GTO[SHORT],
    **{"rfi:UTG": 0.12, "rfi:UTG1": 0.13, "rfi:UTG2": 0.14, "rfi:MP": 0.15,
       "rfi:LJ": 0.16, "rfi:HJ": 0.19, "rfi:CO": 0.25, "rfi:BTN": 0.44, "rfi:SB": 0.40},
    three_bet=0.07, wtsd=0.26,
)


@dataclass(frozen=True)
class GTORow:
    """One stat, the player's rate, and the GTO/benchmark it is read against."""

    stat: str
    player: float
    target: float
    fidelity: str          # EXACT (preflop solver) or BENCHMARK (postflop)
    opps: float

    @property
    def deviation(self) -> float:
        """Signed: positive means the player does it more than optimal."""
        return self.player - self.target


def targets_for(regime: str) -> dict[str, float]:
    return GTO.get(regime, GTO[SHORT])


def _closeness(deviation: float) -> float:
    """1.0 at the target, 0.5 at ``_TOL`` away, smoothly toward 0 beyond."""
    d = abs(deviation)
    return _TOL * _TOL / (_TOL * _TOL + d * d)


def compare(profile, min_opps: float = MIN_OPPS) -> list[GTORow]:
    """Every stat this profile holds that has a GTO/benchmark target and a
    sample worth reading, widest gap first."""
    targets = targets_for(profile.regime)
    rows: list[GTORow] = []
    for stat, target in targets.items():
        est = profile.stats.get(stat)
        if est is None or est.opps < min_opps:
            continue
        fidelity = EXACT if stat in _PREFLOP else BENCHMARK
        rows.append(GTORow(stat, round(est.value, 4), target, fidelity, est.opps))
    rows.sort(key=lambda r: -abs(r.deviation))
    return rows


def rating(rows: list[GTORow]) -> float | None:
    """0-100 closeness to the baseline, or ``None`` below any sample.

    Exact (preflop solver) rows weigh double the benchmark (postflop) rows, so
    the rating leans on the part of the comparison that is actually exact."""
    if not rows:
        return None
    num = den = 0.0
    for r in rows:
        weight = 2.0 if r.fidelity == EXACT else 1.0
        num += weight * _closeness(r.deviation)
        den += weight
    return round(100 * num / den, 1) if den else None
