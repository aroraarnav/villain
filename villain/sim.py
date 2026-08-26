"""A seat at the table: you against villains who play like their profiles.

Wraps the :mod:`villain.holdem` engine and the :mod:`villain.botplay` policy
into a session you can sit in. It rotates the button, tops a short stack back
up so the reps keep coming, runs every villain automatically until the action
is on you, and serialises a state the UI can draw -- with the villains' cards
hidden until a showdown actually turns them over.
"""

from __future__ import annotations

import numpy as np

from .botplay import decide, think_ms, think_pace
from .cards import card_text, describe, evaluate
from .holdem import STREETS, Hand, Seat
from .priors import regime as regime_of

#: Hands a villain needs *in a table size* before that book is preferred to
#: their pooled one. Below it the pooled profile is the better estimate even
#: though it describes the wrong game, because a regime book on forty hands
#: describes nothing at all.
MIN_REGIME_HANDS = 150


class Villain:
    """A player's profiles, and the one that matches the table being played.

    Regime is part of the identity of a book, not a label on it -- the same
    person heads-up and six-handed is two strategies, and pooling them
    describes neither. The sim was handing the policy the pooled profile, so
    a villain defended their big blind at their six-max rate in a heads-up
    match. Seats empty out as people bust, so this is resolved per hand
    rather than once when the game is made.
    """

    def __init__(self, pooled, by_regime: dict | None = None):
        self.pooled = pooled
        self.by_regime = by_regime or {}

    def at(self, players: int):
        book = self.by_regime.get(regime_of(float(players)))
        if book is not None and book.hands >= MIN_REGIME_HANDS:
            return book
        return self.pooled

    @property
    def regimes(self) -> dict:
        return {r: b.hands for r, b in self.by_regime.items()}


class Game:
    def __init__(self, names: list[str], profiles: list, hero_seat: int,
                 start_stack: int, sb: int, bb: int, seed: int | None = None):
        self.names = names
        self.profiles = profiles            # per seat; None for the hero seat
        self.hero_seat = hero_seat
        self.start_stack = start_stack
        self.sb, self.bb = sb, bb
        self.rng = np.random.default_rng(seed)
        self.stacks = [start_stack] * len(names)
        self.button = len(names) - 1        # so the first deal makes seat 0 the SB-ish
        self.hand_no = 0
        self.hand: Hand | None = None
        self.history: list[str] = []
        self.pnl = 0                          # your cumulative session profit/loss
        self._hero_start = start_stack
        self.vs = {n: 0 for i, n in enumerate(names) if i != hero_seat}
        self.results: list[dict] = []
        self.stats = {"hands": 0, "vpip": 0, "pfr": 0,
                      "post_aggr": 0, "post_acts": 0, "sd_seen": 0, "sd_won": 0}
        self.new_hand()

    # -- hand lifecycle ------------------------------------------------------

    def new_hand(self) -> None:
        # Keep the game going: anyone too short to post is topped back up.
        self.stacks = [s if s >= self.bb else self.start_stack for s in self.stacks]
        self._hero_start = self.stacks[self.hero_seat]
        self.button = (self.button + 1) % len(self.names)      # button and blinds move
        seats = [Seat(n, self.stacks[i]) for i, n in enumerate(self.names)]
        self.hand = Hand(seats, self.button, self.sb, self.bb, self.rng)
        self.hand.hero_seat = self.hero_seat
        self.hand_no += 1
        self._starts = list(self.stacks)
        self._hero_vol = self._hero_pfr = False
        self._hero_post_aggr = self._hero_post_acts = 0

    def step(self) -> dict | None:
        """Advance exactly one villain's action, or ``None`` if it is your turn
        or the hand is over. Returns what they did and why -- the UI paces these
        so you can watch the table play."""
        h = self.hand
        if h.over or h.to_act == self.hero_seat:
            return None
        seat = h.to_act
        # Table size, not how many are still in the pot: regime is about how
        # many people you are playing against, which does not change when
        # somebody folds.
        who = self.profiles[seat]
        book = who.at(len(h.seats)) if isinstance(who, Villain) else who
        kind, amount, reason = decide(h, seat, book, self.rng,
                                      self.names[seat])
        ms = think_ms(book, kind, h.street, reason, self.rng)
        act_tag = "aggro" if kind == "raise" else kind
        st_label = "pf" if h.street == 0 else STREETS[h.street]
        h.last_think[seat] = (think_pace(book, ms), st_label, act_tag)
        opening = h.street > 0 and h.bet == 0 and kind == "raise"
        h.act(kind, amount)
        if h.over:
            self._bank()
        return {"seat": seat, "name": self.names[seat], "action": kind,
                "amount": amount, "reason": reason, "opening": opening,
                "think_ms": ms}

    def act(self, kind: str, amount: int = 0) -> None:
        if self.hand is None or self.hand.over or self.hand.to_act != self.hero_seat:
            raise RuntimeError("not your turn")
        street = self.hand.street
        self.hand.act(kind, amount)
        if street == 0:
            if kind in ("call", "raise"):
                self._hero_vol = True
            if kind == "raise":
                self._hero_pfr = True
        else:
            self._hero_post_acts += 1
            if kind == "raise":
                self._hero_post_aggr += 1
        if self.hand.over:
            self._bank()

    def _bank(self) -> None:
        seats = self.hand.seats
        ends = [s.stack for s in seats]
        for i, name in enumerate(self.names):
            if i != self.hero_seat:
                self.vs[name] = self.vs.get(name, 0) + (ends[i] - self._starts[i])
        hero_net = ends[self.hero_seat] - self._starts[self.hero_seat]
        self.pnl += hero_net
        showdown = len(self.hand._in_hand()) > 1
        hero_folded = seats[self.hero_seat].folded
        hero_won = (self.hand.winners or {}).get(self.hero_seat, 0) > 0
        st = self.stats
        st["hands"] += 1
        st["vpip"] += int(self._hero_vol)
        st["pfr"] += int(self._hero_pfr)
        st["post_aggr"] += self._hero_post_aggr
        st["post_acts"] += self._hero_post_acts
        if showdown and not hero_folded:
            st["sd_seen"] += 1
            st["sd_won"] += int(hero_won)
        self.results.append({"hand_no": self.hand_no, "net": hero_net,
                             "pot": sum(s.hand_put for s in seats)})
        self.stacks = ends
        for line in self.hand.log:
            self.history.append(line)

    def analysis(self) -> dict:
        """A read on the session so far: your line, your P/L, and how you did
        against each villain."""
        st = self.stats
        n = max(st["hands"], 1)
        bb = self.bb
        def pct(a, b):
            return round(100 * a / b, 1) if b else None
        results = sorted(self.results, key=lambda r: r["net"])
        return {
            "hands": st["hands"],
            "pnl": self.pnl,
            "pnl_bb": round(self.pnl / bb, 1) if bb else 0,
            "bb100": round((self.pnl / bb) / n * 100, 1) if bb else 0,
            "vpip": pct(st["vpip"], st["hands"]),
            "pfr": pct(st["pfr"], st["hands"]),
            "aggression": pct(st["post_aggr"], st["post_acts"]),
            "went_to_showdown": pct(st["sd_seen"], st["hands"]),
            "won_at_showdown": pct(st["sd_won"], st["sd_seen"]),
            "vs": sorted(
                [{"name": k, "net": v, "net_bb": round(v / bb, 1) if bb else 0}
                 for k, v in self.vs.items()], key=lambda d: d["net"]),
            "worst": results[0] if results else None,
            "best": results[-1] if results else None,
            "lessons": self._lessons(),
        }

    def _lessons(self) -> list[str]:
        """Name the reads this session was supposed to teach.

        The analysis used to be P/L and VPIP -- numbers that do not say
        *what to do next*. These sentences are the profile talking: they
        fold this many flops, they fold this many 3-bets, so this is the
        work.
        """
        n = max(self.stats["hands"], 1)
        aggr = (self.stats["post_aggr"] / self.stats["post_acts"]
                if self.stats["post_acts"] else None)
        out = []
        for i, who in enumerate(self.profiles):
            if i == self.hero_seat or who is None:
                continue
            book = who.at(len(self.names)) if isinstance(who, Villain) else who
            name = self.names[i]
            fold_f = _stat(book, "fold_vs_bet:flop")
            if fold_f is not None and fold_f >= 0.55:
                if aggr is not None and aggr < 0.40:
                    out.append(f"{name} folds {fold_f:.0%} of flops — you bet "
                               f"{aggr:.0%} of postflop streets. Bet them.")
                else:
                    out.append(f"{name} folds {fold_f:.0%} of flops — keep betting.")
            elif fold_f is not None and fold_f <= 0.30:
                out.append(f"{name} continues {1 - fold_f:.0%} of flops — "
                           "value thinner, stop bluffing.")
            fold_t = _stat(book, "fold_vs_bet:turn")
            if fold_t is not None and fold_t >= 0.55:
                out.append(f"{name} folds {fold_t:.0%} of turns — the second barrel prints.")
            f3 = _stat(book, "fold_to_three_bet")
            if f3 is not None and f3 >= 0.65:
                out.append(f"{name} folds {f3:.0%} to 3-bets — 3-bet them light.")
            cbet = _stat(book, "cbet:flop")
            if cbet is not None and cbet >= 0.72:
                out.append(f"{name} c-bets {cbet:.0%} of flops — most of it is air. Raise and float.")
        # Session-level, once, so a 2-hand session is not a lecture.
        if n >= 8 and self.stats["vpip"] / n < 0.15:
            out.append("You played fewer than 15% of hands. The reps are in the pots you take.")
        return out[:5]

    # -- view ----------------------------------------------------------------

    def state(self) -> dict:
        h = self.hand
        showdown = h.over and len(h._in_hand()) > 1
        seats = []
        for i, s in enumerate(h.seats):
            reveal = i == self.hero_seat or (showdown and not s.folded)
            seats.append({
                "name": s.name,
                "stack": s.stack,
                "committed": s.street_put,
                "folded": s.folded,
                "all_in": s.all_in,
                "is_hero": i == self.hero_seat,
                "is_button": i == self.button,
                "to_act": (not h.over) and h.to_act == i,
                # Gross pot pushed to them, and what the hand actually made.
                # The badge next to a stack has to be the second one: showing
                # the gross "+200" beside a stack that only rose by 100 reads
                # as profit and is not.
                "won": (h.winners or {}).get(i, 0),
                "net": ((h.winners or {}).get(i, 0) - s.hand_put) if h.over else 0,
                "hole": [card_text(c) for c in s.hole] if reveal else None,
                "all_hole": [card_text(c) for c in s.hole] if h.over else None,
                # Only you: villains are face-down, so a made-hand label on
                # them would be a tell. Empty preflop so the seat height does
                # not jump when the flop names it.
                "made": _made_name(s.hole, h.board) if i == self.hero_seat else None,
            })
        legal = None
        if not h.over and h.to_act == self.hero_seat:
            lg = h.legal()
            legal = {
                "can_check": lg.can_check, "can_call": lg.can_call,
                "call_amount": lg.call_amount, "can_raise": lg.can_raise,
                "min_raise_to": lg.min_raise_to, "max_raise_to": lg.max_raise_to,
                "can_fold": lg.can_fold, "committed": h.seats[self.hero_seat].street_put,
            }
        return {
            "hand_no": self.hand_no,
            "pnl": self.pnl,
            "sb": self.sb, "bb": self.bb,
            "street": STREETS[h.street],
            "board": [card_text(c) for c in h.board],
            "pot": h.pot,
            # In the middle of the table: only what has been gathered.
            # This street's chips stay in front of the seats until the
            # round closes, the way a live pot does not eat the bets.
            "pot_mid": h.pot_settled,
            "button": self.button,
            "hero_seat": self.hero_seat,
            "seats": seats,
            "over": h.over,
            "showdown": showdown,
            "your_turn": (not h.over) and h.to_act == self.hero_seat,
            "legal": legal,
            "log": list(h.log),
            "ranges": self._range_review() if h.over else None,
        }

    def _range_review(self) -> dict:
        """What each villain still in the pot can hold, for the post-hand panel.

        Folded seats are out: listing a preflop folder's junk (or a river
        folder's tens) as "what they can still hold" is how QT on a ten-high
        board showed up next to a fold.
        """
        h = self.hand
        rs = getattr(h, "_ranges", None)
        if rs is None:
            return {}
        out = {}
        board = h.board or None
        for i, s in enumerate(h.seats):
            if i == self.hero_seat or s.folded:
                continue
            rows = (rs.top_made(i, board, 8) if board and len(h.board) >= 3
                    else rs.top_classes(i, 8, board))
            if rows:
                out[s.name] = [{"cls": n, "share": round(p, 3)} for n, p in rows]
        return out


def _made_name(hole, board) -> str | None:
    """Category of this hole on this board: pair, flush, full house, …

    Needs five cards, so nothing until the flop. The evaluator already
    scores a 5- or 6-card board; we do not pad.
    """
    if not hole or len(board) < 3:
        return None
    seven = np.concatenate([
        np.array(hole, dtype=np.int64),
        np.array(board, dtype=np.int64),
    ])
    return describe(int(evaluate(seven[None, :])[0]))


def _stat(profile, key: str, min_opps: float = 20.0) -> float | None:
    value = None if profile is None else profile.rate(key, None, min_opps)
    return None if value is None else float(value)
