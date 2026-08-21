"""Hand strength, learned from revealed cards.

It refuses to run on too little data, and that refusal is the behavior worth
testing hardest -- a model fitted to a handful of showdowns will produce
predictions, and they will be noise.
"""

import pytest

from villain.reads import MIN_ROWS, build_dataset, fit, texture
from villain.reads import NotEnoughData as ReadsNotEnoughData


def test_strength_dataset_labels_only_known_cards(hands):
    rows = build_dataset(hands)
    assert rows
    assert all(0.0 <= r.strength <= 1.0 for r in rows)
    assert all(len(r.features) == 17 for r in rows)
    # Folds are excluded: a folded hand has no strength worth predicting.
    assert "fold" not in {r.action for r in rows}


def test_strength_model_refuses_thin_data(hands):
    rows = build_dataset(hands)
    if len(rows) < MIN_ROWS:
        with pytest.raises(ReadsNotEnoughData, match="keep importing"):
            fit(rows)


def test_strength_model_fits_and_predicts(hands):
    rows = build_dataset(hands) * 30      # shape check only, not a statistical claim
    model = fit(rows)
    assert model.rows == len(rows)
    assert 0.0 <= model.mae <= 0.5
    prediction = model.predict(rows[0].features)
    assert 0.0 <= prediction <= 1.0


def test_unbiased_rows_are_marked(hands):
    """The exporting player's cards are visible without a showdown; villains' are not."""
    rows = build_dataset(hands)
    assert any(r.unbiased for r in rows)


@pytest.mark.parametrize("board,expected", [
    (["2c", "2d", "9h"], (1.0, 0.0, 0.0, 0.0)),
    (["2c", "5c", "9c"], (0.0, 1.0, 0.0, 0.0)),
    (["7c", "8d", "9h"], (0.0, 0.0, 1.0, 0.0)),
    (["Ac", "8d", "2h"], (0.0, 0.0, 0.0, 1.0)),
])
def test_board_texture(board, expected):
    assert texture(board) == expected
