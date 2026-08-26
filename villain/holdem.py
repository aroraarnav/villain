"""A small, correct no-limit Hold'em engine -- enough to get reps in.

One :class:`Hand` runs a single hand as a state machine driven from outside:
it deals, posts blinds, and then exposes whose turn it is (:attr:`to_act`),
what they may legally do (:meth:`legal`), and takes one action at a time
(:meth:`act`). It never decides an action itself -- a UI or a villain policy
does that -- so the same engine drives a human seat and an AI seat identically.

Kept deliberately lean, but correct where correctness is not optional:

* **Side pots.** An all-in for less than a bet builds a side pot the short
  stack cannot win; pots are peeled by contribution level and awarded
  separately (:meth:`_settle`).
* **Min-raise.** A raise is at least the size of the previous bet or raise; an
  all-in shorter than that does not re-open action for players already square.
* **Heads-up blinds.** The button posts the small blind and acts first
  preflop, last after.

Showdown uses :func:`villain.cards.evaluate`, the same evaluator the reads are
built on. Chips are plain integers in whatever unit the caller passes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cards import evaluate

STREETS = ("preflop", "flop", "turn", "river")


@dataclass
class Seat:
    name: str
    stack: int
    hole: tuple[int, ...] = ()
    folded: bool = False
    all_in: bool = False
    street_put: int = 0        # chips committed on the current street
    hand_put: int = 0          # chips committed across the whole hand

    @property
    def live(self) -> bool:
        """Still able to act -- in the hand and with chips behind."""
        return not self.folded and not self.all_in


@dataclass
class Legal:
    """What the seat to act may do, and the amounts involved."""

    can_check: bool
    can_call: bool
    call_amount: int           # chips to put in to call (0 if checking)
    can_raise: bool
    min_raise_to: int          # smallest total this-street commitment a raise may reach
    max_raise_to: int          # largest (all-in)
    can_fold: bool


class Hand:
    def __init__(self, seats: list[Seat], button: int, sb: int, bb: int,
                 rng: np.random.Generator | None = None):
        self.seats = seats
        self.n = len(seats)
        self.button = button
        self.sb, self.bb = sb, bb
        self.rng = rng or np.random.default_rng()
        self.board: list[int] = []
        self.street = 0
        self.pot_settled = 0            # chips gathered from earlier streets
        self.log: list[str] = []
        self.winners: dict[int, int] | None = None   # seat -> chips won, when over

        deck = list(range(52))
        self.rng.shuffle(deck)
        self._deck = deck
        for s in seats:
            s.hole = (deck.pop(), deck.pop())

        self._post_blinds()
        self.bet = self.bb                        # highest street commitment so far
        self.min_raise = self.bb                  # size of the last full raise
        self.acted: set[int] = set()              # acted since the last full raise
        self.raises = 0                           # raises this street (an open is 1)
        # Preflop shape, for the policies that price an iso-raise or a squeeze.
        # A limp and a cold-call are both "call", and telling them apart needs
        # only whether a raise had gone in yet -- so count them as they happen
        # rather than reconstructing the street afterwards.
        self.limpers = 0                          # preflop calls before any raise
        self.limped: set[int] = set()             # seats that open-limped this hand
        self.callers = 0                          # preflop calls after a raise
        self.last_raiser: int | None = None       # seat of the most recent aggressor
        self.initiative: int | None = None         # who bet last *claimed* street
        # Seats that checked when they held the lead, and have not bet since.
        # Betting later is a delayed c-bet, not a second barrel -- features.py
        # splits those two and the policy has to, or a flop check wipes the
        # lead and the turn never fires the number that was counted for it.
        # Cleared again on a bet, for the same reason: once they have taken
        # the lead back, the next street is an ordinary barrel.
        self.declined_initiative: set[int] = set()
        # A raise shorter than the minimum (an all-in for less) does not
        # re-open action: players who have already acted can only call or
        # fold. Tracked separately from ``acted`` because they still owe the
        # extra chips.
        self.raise_open: bool = True
        # Preflop shape carried onto later streets. c-betting a 3-bet pot is
        # not c-betting a limped one, and the policy used one number for both.
        self.opener: int | None = None            # first preflop raiser
        # Who made the 3-bet. `fold_to_three_bet` belongs to the opener and
        # `fold_to_four_bet` to the 3-bettor -- features counts them on
        # exactly those seats -- so the policy has to know which seat it is
        # talking to before it hands anyone those numbers.
        self.three_bettor: int | None = None
        self.pot_kind: str = "pre"                # limp / srp / 3bp after preflop
        self.called_street: set[int] = set()      # called a bet this street
        self.called_prev: set[int] = set()        # called a bet last street
        self.hero_seat: int | None = None         # set by the session, for vs-you keys
        self.last_think: dict = {}                # seat -> (pace, street, act) for fold_next
        self._staged: tuple[int, object] | None = None   # see :meth:`stage`
        self.to_act: int | None = self._first_to_act_preflop()

    # -- setup ---------------------------------------------------------------

    def _did(self, seat: int, they: str, you: str) -> str:
        """A log clause: third person for a name, first person for You.

        The subject is the seat's display name. ``You calls`` is not a sentence.
        """
        name = self.seats[seat].name
        if seat == getattr(self, "hero_seat", None) or name.lower() == "you":
            return f"{name} {you}"
        return f"{name} {they}"

    def _post_blinds(self) -> None:
        if self.n == 2:
            sb_seat, bb_seat = self.button, self._next(self.button)
        else:
            sb_seat = self._next(self.button)
            bb_seat = self._next(sb_seat)
        self._commit(sb_seat, self.sb)
        self._commit(bb_seat, self.bb)
        self.log.append(self._did(sb_seat, f"posts {self.sb}", f"post {self.sb}"))
        self.log.append(self._did(bb_seat, f"posts {self.bb}", f"post {self.bb}"))

    def _first_to_act_preflop(self) -> int | None:
        if self.n == 2:
            start = self.button                   # SB/button acts first pre
        else:
            start = self._next(self._next(self._next(self.button)))  # UTG
            start = self._prev_wrap_start(start)
        return self._seek_actor(start)

    def _prev_wrap_start(self, idx: int) -> int:
        # _seek_actor starts *at* idx, so hand back the exact UTG seat.
        return idx

    def _first_to_act_postflop(self) -> int | None:
        start = self.button if self.n == 2 else self.button
        return self._seek_actor(self._next(start))

    # -- geometry ------------------------------------------------------------

    def _next(self, idx: int) -> int:
        return (idx + 1) % self.n

    def _seek_actor(self, start: int) -> int | None:
        """First seat from ``start`` (inclusive, clockwise) that still needs to
        act this street, or ``None`` if the round is closed."""
        for step in range(self.n):
            i = (start + step) % self.n
            if self._needs_to_act(i):
                return i
        return None

    def _needs_to_act(self, i: int) -> bool:
        s = self.seats[i]
        if not s.live:
            return False
        return s.street_put < self.bet or i not in self.acted

    # -- chips ---------------------------------------------------------------

    def _commit(self, i: int, to_total: int) -> None:
        """Move a seat's *street* commitment up to ``to_total`` (capped at all-in)."""
        s = self.seats[i]
        want = min(to_total, s.street_put + s.stack)   # cannot exceed the stack
        add = want - s.street_put
        if add <= 0:
            return
        s.stack -= add
        s.street_put += add
        s.hand_put += add
        if s.stack == 0:
            s.all_in = True

    # -- queries -------------------------------------------------------------

    @property
    def pot(self) -> int:
        return self.pot_settled + sum(s.street_put for s in self.seats)

    @property
    def over(self) -> bool:
        return self.winners is not None

    def _in_hand(self) -> list[int]:
        return [i for i, s in enumerate(self.seats) if not s.folded]

    def legal(self) -> Legal:
        if self.to_act is None:
            raise RuntimeError("no seat to act")
        i = self.to_act
        s = self.seats[i]
        owed = self.bet - s.street_put
        can_check = owed == 0
        call_amount = min(owed, s.stack)
        can_call = owed > 0 and s.stack > 0
        # A raise has to reach at least the current bet plus a full raise, but
        # never more than the seat can put in.
        max_raise_to = s.street_put + s.stack
        min_raise_to = self.bet + self.min_raise
        # Incomplete all-ins do not re-open: a player who has already acted
        # on the last *full* raise may call the extra or fold, not raise.
        can_raise = (max_raise_to > self.bet and s.stack > owed
                     and (self.raise_open or i not in self.acted))
        return Legal(
            can_check=can_check, can_call=can_call, call_amount=call_amount,
            can_raise=can_raise, min_raise_to=min(min_raise_to, max_raise_to),
            max_raise_to=max_raise_to, can_fold=owed > 0)

    # -- actions -------------------------------------------------------------

    def stage(self, seat: int, commit) -> None:
        """Hold a callback to run if ``seat``'s pending decision is played.

        Picking an action tells you which slice of a seat's range would have
        acted that way -- but only if it is actually played, and a caller may
        ask what two profiles would do at one node without playing either. So
        the policy stages the update and :meth:`act` commits it. Staging again
        replaces the slot.
        """
        self._staged = (seat, commit)

    def act(self, kind: str, amount: int = 0) -> None:
        """Apply the seat-to-act's decision. ``kind`` is ``fold``/``check``/
        ``call``/``raise``; ``amount`` for a raise is the total this-street
        commitment to reach (a raise *to*, not *by*)."""
        if self.to_act is None:
            raise RuntimeError("no seat to act")
        i = self.to_act
        s = self.seats[i]
        legal = self.legal()
        staged = getattr(self, "_staged", None)
        self._staged = None
        if staged is not None and staged[0] == i:
            staged[1]()

        if kind == "fold":
            s.folded = True
            self.log.append(self._did(i, "folds", "fold"))
        elif kind == "check":
            if not legal.can_check:
                raise ValueError("cannot check facing a bet")
            self.acted.add(i)
            if self.initiative == i:
                self.declined_initiative.add(i)
            self.log.append(self._did(i, "checks", "check"))
        elif kind == "call":
            if self.street == 0:
                if self.raises == 0:
                    self.limpers += 1
                    self.limped.add(i)
                else:
                    self.callers += 1
            if self.raises >= 1:
                self.called_street.add(i)
            self._commit(i, self.bet)
            self.acted.add(i)
            self.log.append(self._did(i, "calls", "call"))
        elif kind == "raise":
            if not legal.can_raise:
                raise ValueError("cannot raise")
            to = max(min(amount, legal.max_raise_to), 1)
            if to < legal.min_raise_to and to != legal.max_raise_to:
                raise ValueError("raise below the minimum")
            prev_bet = self.bet
            self._commit(i, to)
            actual = self.seats[i].street_put
            # Use chips actually in, not the requested ``to``: an all-in that
            # asked for a full raise but could not make the number is still
            # incomplete, and must not re-open players already square.
            full = actual - prev_bet >= self.min_raise
            if full:
                self.min_raise = actual - prev_bet
                self.bet = actual
                self.acted = {i}                        # everyone else owes action again
                self.raise_open = True
            else:
                self.bet = max(self.bet, actual)
                self.acted.add(i)
                self.raise_open = False
            self.raises += 1
            self.last_raiser = i
            self.declined_initiative.discard(i)
            if self.street == 0 and self.opener is None:
                self.opener = i
            elif self.street == 0 and self.raises == 2 and self.three_bettor is None:
                self.three_bettor = i
            # Opening an unbet street is a bet. The engine has no separate
            # verb -- a raise from zero is how a flop stab is applied -- but
            # the log saying "raises to 79" after two checks is how a river
            # opener read as a reraise.
            if prev_bet == 0:
                self.log.append(self._did(i, f"bets {actual}", f"bet {actual}"))
            else:
                self.log.append(self._did(i, f"raises to {actual}", f"raise to {actual}"))
        else:
            raise ValueError(f"unknown action {kind!r}")

        self._advance(i)

    def _advance(self, from_i: int) -> None:
        # One player left un-folded: hand is over, no showdown.
        alive = self._in_hand()
        if len(alive) == 1:
            self._settle()
            return
        nxt = self._seek_actor(self._next(from_i))
        if nxt is not None:
            self.to_act = nxt
            return
        # Betting round closed. Gather the street into the pot and move on.
        self.pot_settled += sum(s.street_put for s in self.seats)
        for s in self.seats:
            s.street_put = 0
        if self.street == 0:
            if self.raises >= 2:
                self.pot_kind = "3bp"
            elif self.raises >= 1:
                self.pot_kind = "srp"
            else:
                self.pot_kind = "limp"
        self.called_prev = set(self.called_street)
        self.called_street = set()
        self.acted = set()
        self.bet = 0
        self.min_raise = self.bb
        self.raises = 0
        self.callers = 0
        self.raise_open = True
        # A checked street does not wipe the lead. Features counts a delayed
        # c-bet as "they took the last *claimed* street and checked this one";
        # assigning ``last_raiser`` (None after a check-through) here made
        # that number unplayable -- the turn thought nobody had the lead.
        if self.last_raiser is not None:
            self.initiative = self.last_raiser
        self.last_raiser = None
        # If at most one player can still act, run the board out to showdown.
        if sum(1 for i in alive if self.seats[i].live) <= 1:
            self._runout()
            return
        self._next_street()

    def _next_street(self) -> None:
        if self.street == 3:
            self._settle()
            return
        self.street += 1
        draws = 3 if self.street == 1 else 1
        for _ in range(draws):
            self.board.append(self._deck.pop())
        self.log.append(f"{STREETS[self.street]}: "
                        f"{' '.join(_card(c) for c in self.board)}")
        self.to_act = self._first_to_act_postflop()
        if self.to_act is None:                          # everyone all-in
            self._runout()

    def _runout(self) -> None:
        while len(self.board) < 5:
            self.board.append(self._deck.pop())
        self._settle()

    # -- payouts -------------------------------------------------------------

    def _settle(self) -> None:
        self.pot_settled += sum(s.street_put for s in self.seats)
        for s in self.seats:                             # sweep last street in
            s.street_put = 0
        contribs = {i: s.hand_put for i, s in enumerate(self.seats)}
        winners: dict[int, int] = dict.fromkeys(range(self.n), 0)

        # Peel side pots by contribution level.
        levels = sorted({c for c in contribs.values() if c > 0})
        prev = 0
        for lvl in levels:
            layer = lvl - prev
            contributors = [i for i, c in contribs.items() if c >= lvl]
            amount = layer * len(contributors)
            contenders = [i for i in contributors if not self.seats[i].folded]
            if not contenders:                           # everyone folded in -- rare
                contenders = contributors
            best = self._best(contenders)
            share, extra = divmod(amount, len(best))
            for j, w in enumerate(best):
                winners[w] += share + (1 if j < extra else 0)
            prev = lvl

        for i, won in winners.items():
            self.seats[i].stack += won
        self.winners = {i: w for i, w in winners.items() if w > 0}
        for i, w in self.winners.items():
            self.log.append(self._did(i, f"wins {w}", f"win {w}"))

    def _best(self, seats: list[int]) -> list[int]:
        """The seat(s) with the strongest seven-card hand; ties share."""
        if len(seats) == 1:
            return seats
        board = np.array(self.board, dtype=np.int64)
        scores = {}
        for i in seats:
            seven = np.concatenate([np.array(self.seats[i].hole, dtype=np.int64), board])
            scores[i] = int(evaluate(seven[None, :])[0])
        top = max(scores.values())
        return [i for i in seats if scores[i] == top]


def _card(cid: int) -> str:
    from .cards import card_text
    return card_text(cid)
