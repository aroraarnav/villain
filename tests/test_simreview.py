"""The practice-session review: decisions graded against the cards the villains
really held, and advice that points at the hands it came from."""

import re
from pathlib import Path

import numpy as np
from helpers import Prof

from villain.glossary import TERMS, stat_help
from villain.sim import Game
from villain.simreview import _grade, _keep_stop, _villain, hand_detail, review


def _hand(hero_hole, their_hole, board, flop_actions, *, hand_no=1, showdown=False,
          hero_won=False, street=1):
    """A two-seat hand record in the shape Game._bank writes: preflop raise
    and call, then whatever flop actions the test names."""
    pre = [
        {"seat": 0, "street": 0, "kind": "raise", "owed": 1, "pot": 3,
         "raises": 0, "aggressor": None},
        {"seat": 1, "street": 0, "kind": "call", "owed": 4, "pot": 9,
         "raises": 1, "aggressor": 0},
    ]
    return {
        "hand_no": hand_no, "net": 0, "pot": 12, "board": board,
        "street": street, "showdown": showdown,
        "seats": [
            {"name": "You", "position": "BTN", "hole": hero_hole, "folded": False,
             "won": hero_won, "net": 0},
            {"name": "PlayerA", "position": "BB", "hole": their_hole, "folded": False,
             "won": not hero_won, "net": 0},
        ],
        "actions": pre + flop_actions,
        "log": [],
    }


def _facing_bet(kind):
    return [
        {"seat": 1, "street": 1, "kind": "raise", "owed": 0, "pot": 12,
         "raises": 0, "aggressor": 0},
        {"seat": 0, "street": 1, "kind": kind, "owed": 8, "pot": 20,
         "raises": 1, "aggressor": 1},
    ]


def test_folding_the_best_hand_is_flagged():
    """Aces folded to a bet from seven-deuce on a king-high flop."""
    h = _hand(["As", "Ad"], ["7c", "2d"], ["Kh", "8s", "3c"], _facing_bet("fold"))
    (d,) = _grade(h, 0)
    assert d["kind"] == "fold" and not d["right"]
    assert d["equity"] > 0.9 and d["needed"] == round(8 / 28, 3)


def test_folding_the_worst_hand_is_right():
    h = _hand(["7c", "2d"], ["As", "Ad"], ["Kh", "8s", "3c"], _facing_bet("fold"))
    (d,) = _grade(h, 0)
    assert d["kind"] == "fold" and d["right"]


def test_calling_a_bet_you_cannot_beat_is_paying_off():
    h = _hand(["7c", "2d"], ["As", "Ad"], ["Kh", "8s", "3c"], _facing_bet("call"))
    (d,) = _grade(h, 0)
    assert d["kind"] == "call" and not d["right"]


def test_multiway_folds_are_not_graded():
    """Beating the bettor says nothing about the third player still in."""
    h = _hand(["As", "Ad"], ["7c", "2d"], ["Kh", "8s", "3c"], _facing_bet("fold"))
    h["seats"].append({"name": "PlayerB", "position": "SB", "hole": ["Qs", "Qd"],
                       "folded": False, "won": False, "net": 0})
    assert _grade(h, 0) == []


def test_a_no_pair_bet_that_takes_the_pot_worked():
    bet = [{"seat": 0, "street": 1, "kind": "raise", "owed": 0, "pot": 12,
            "raises": 0, "aggressor": 0},
           {"seat": 1, "street": 1, "kind": "fold", "owed": 8, "pot": 20,
            "raises": 1, "aggressor": 0}]
    h = _hand(["Qc", "Jd"], ["7c", "2d"], ["Kh", "8s", "3c"], bet, hero_won=True)
    (d,) = _grade(h, 0)
    assert d["kind"] == "bluff" and d["right"]


def test_folding_the_best_hand_becomes_a_stop_tip_citing_the_hand():
    h = _hand(["As", "Ad"], ["7c", "2d"], ["Kh", "8s", "3c"], _facing_bet("fold"),
              hand_no=7)
    keep, stop = _keep_stop([h], 0, _grade(h, 0))
    assert any("price" in t["text"] and t["hands"] == [7] for t in stop)
    assert not keep


def test_villain_tip_names_their_bluffs_you_folded_to():
    """Their cards are face up now, so "they had nothing" is a fact."""
    hands = [_hand(["As", "Kd"], ["7c", "2d"], ["Qh", "8s", "3c"], _facing_bet("fold"),
                   hand_no=n) for n in (3, 5)]
    v = _villain(hands, 0, 1, "PlayerA", 2)
    assert v["their_bets"] == {"n": 2, "you_folded": 2, "no_pair": 2}
    assert any(t["hands"] == [3, 5] for t in v["dont"])


def _session(n_hands=150, seed=3):
    profs = [Prof(vpip=0.6, pfr=0.15), Prof(vpip=0.12, pfr=0.1)]
    g = Game(["You", "PlayerA", "PlayerB"], [None, *profs], 0, 200, 1, 2, seed=seed)
    rng = np.random.default_rng(seed)
    for _ in range(n_hands):
        while not g.hand.over:
            if g.hand.to_act == 0:
                lg = g.hand.legal()
                opts = [k for k, ok in (("fold", lg.can_fold and not lg.can_check),
                                        ("check", lg.can_check), ("call", lg.can_call),
                                        ("raise", lg.can_raise)) if ok]
                kind = opts[rng.integers(len(opts))]
                g.act(kind, lg.min_raise_to if kind == "raise" else 0)
            else:
                g.step()
        g.new_hand()
    return g


def test_review_of_a_real_session_is_consistent():
    g = _session()
    a = review(g)
    assert a["hands"] == len(g.hands) == 150
    assert a["pnl"] == sum(h["net"] for h in g.hands)
    by_kind = a["decisions"]
    assert a["graded"]["decisions"] == sum(d["n"] for d in by_kind.values())
    assert a["graded"]["right"] == sum(d["right"] for d in by_kind.values())
    assert sum(c["dealt"] for c in a["grid"].values()) == 150
    assert sum(p["hands"] for p in a["positions"]) == 150
    played = sum(c["played"] for c in a["grid"].values())
    assert played == next(r["hits"] for r in a["line"] if r["stat"] == "vpip")
    assert len(a["key_hands"]) <= 8
    # Every hand a tip cites is a hand of this session.
    numbers = {h["hand_no"] for h in g.hands}
    tips = a["keep"] + a["stop"] + [t for v in a["villains"] for t in v["do"] + v["dont"]]
    assert all(set(t["hands"]) <= numbers for t in tips)
    # A zero-sum table: what you made, they lost.
    assert a["pnl"] + sum(v["net"] for v in a["villains"]) == 0


def test_every_session_number_explains_itself():
    a = review(_session(40))
    missing = [r["stat"] for r in a["line"] if not stat_help(r["stat"])]
    missing += [v[k]["stat"] for v in a["villains"] for k in ("vpip", "pfr")
                if not stat_help(v[k]["stat"])]
    assert not missing


def test_every_term_the_review_names_is_defined():
    js = (Path(__file__).parents[1] / "villain/webapp/assets/app/42-review.js").read_text()
    terms = set(re.findall(r'termTip\("([^"]+)"\)', js))
    terms |= set(re.findall(r'\["\w+", "[^"]+", "([^"]+)"\]', js))
    assert terms and not terms - set(TERMS)


def test_a_hand_opens_face_up():
    g = _session(20)
    d = hand_detail(g, 5)
    assert d["hand_no"] == 5 and d["log"]
    assert all(len(s["hole"]) == 2 for s in d["seats"])
    assert hand_detail(g, 999) is None


def test_the_review_routes_open_a_session_hand(seeded, db):
    """End and analyze, then open a cited hand -- through the same bridge the
    hosted app calls."""
    import json

    from villain.webapp import browser
    from villain.webapp.server import SIM_GAMES

    browser.set_db(str(db))
    pid = next(int(r["id"]) for r in seeded.players())
    made = browser.dispatch_json("POST", "/api/sim/new", json.dumps({"villains": [pid]}))
    token = json.loads(made["body"])["token"]
    game = SIM_GAMES[token]
    for _ in range(6):
        while not game.hand.over:
            if game.hand.to_act == game.hero_seat:
                lg = game.hand.legal()
                game.act("check" if lg.can_check else "call")
            else:
                game.step()
        game.new_hand()

    a = browser.dispatch_json("POST", "/api/sim/analysis", json.dumps({"token": token}))
    assert a["status"] == 200 and a["wrote"] is False
    assert json.loads(a["body"])["analysis"]["hands"] == 6

    opened = browser.dispatch_json("POST", "/api/sim/hand",
                                   json.dumps({"token": token, "hand_no": 2}))
    assert opened["status"] == 200 and opened["wrote"] is False
    assert json.loads(opened["body"])["log"]
    gone = browser.dispatch_json("POST", "/api/sim/hand",
                                 json.dumps({"token": token, "hand_no": 99}))
    assert gone["status"] == 404
