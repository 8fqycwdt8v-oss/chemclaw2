"""Tests for the Elo update used by the hypothesis tournament ranker.

Pure-Python helper — no DB, no network, no fixtures. Locks down the
expected behaviour of `elo_update` so future tweaks to K-factor or
draw handling don't silently shift the ranking signal.
"""
from __future__ import annotations

import math

import pytest

from api.db.queries.hypotheses import DEFAULT_K_FACTOR, elo_update


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return math.isclose(a, b, abs_tol=tol)


def test_equal_ratings_winner_takes_half_K() -> None:
    """At equal ratings, expected score is 0.5 each, so a win moves the
    winner by exactly K/2 and the loser by -K/2."""
    new_a, new_b = elo_update(1000.0, 1000.0, "a")
    assert _approx(new_a, 1000.0 + DEFAULT_K_FACTOR / 2)
    assert _approx(new_b, 1000.0 - DEFAULT_K_FACTOR / 2)


def test_equal_ratings_tie_no_movement() -> None:
    new_a, new_b = elo_update(1000.0, 1000.0, "tie")
    assert _approx(new_a, 1000.0)
    assert _approx(new_b, 1000.0)


def test_symmetry_a_wins_equals_b_loses() -> None:
    """The amount a's rating goes up on a win must equal the amount b's
    rating goes down. Zero-sum — Elo's defining property."""
    new_a, new_b = elo_update(1200.0, 1300.0, "a")
    gain_a = new_a - 1200.0
    loss_b = 1300.0 - new_b
    assert _approx(gain_a, loss_b)


def test_upset_pays_more_than_expected_win() -> None:
    """Lower-rated player beating a higher-rated one gains MORE than
    a higher-rated player beating a lower-rated one."""
    new_low, _ = elo_update(1000.0, 1500.0, "a")  # 500-rating upset
    new_high, _ = elo_update(1500.0, 1000.0, "a")  # 500-rating expected win
    upset_gain = new_low - 1000.0
    expected_gain = new_high - 1500.0
    assert upset_gain > expected_gain
    # Upset > 16 (the K/2 figure for a 50-50 matchup) — favourite gain < 16.
    assert upset_gain > DEFAULT_K_FACTOR / 2
    assert expected_gain < DEFAULT_K_FACTOR / 2


def test_losing_player_cannot_gain() -> None:
    new_a, new_b = elo_update(1000.0, 1000.0, "b")
    assert new_a < 1000.0
    assert new_b > 1000.0


def test_tie_pulls_higher_rated_down_slightly() -> None:
    """A tie against a lower-rated opponent is worse than expected for the
    favourite — their rating should drop a little, the underdog's should
    rise."""
    new_high, new_low = elo_update(1500.0, 1000.0, "tie")
    assert new_high < 1500.0
    assert new_low > 1000.0


def test_invalid_winner_raises() -> None:
    with pytest.raises(ValueError, match="winner must be"):
        elo_update(1000.0, 1000.0, "draw")  # not 'tie'


def test_invalid_k_raises() -> None:
    with pytest.raises(ValueError, match="k must be > 0"):
        elo_update(1000.0, 1000.0, "a", k=0)
    with pytest.raises(ValueError, match="k must be > 0"):
        elo_update(1000.0, 1000.0, "a", k=-1.0)


def test_k_factor_scales_movement_linearly() -> None:
    """Doubling K doubles every rating delta for the same matchup."""
    a_low_k, b_low_k = elo_update(1000.0, 1200.0, "a", k=16.0)
    a_high_k, b_high_k = elo_update(1000.0, 1200.0, "a", k=32.0)
    assert _approx(a_high_k - 1000.0, 2 * (a_low_k - 1000.0))
    assert _approx(1200.0 - b_high_k, 2 * (1200.0 - b_low_k))


def test_repeated_ties_drive_ratings_toward_each_other() -> None:
    """Running the update repeatedly with ties should monotonically
    shrink the gap between two ratings (no oscillation)."""
    a, b = 1500.0, 1000.0
    gap = abs(a - b)
    for _ in range(50):
        a, b = elo_update(a, b, "tie")
        new_gap = abs(a - b)
        assert new_gap < gap
        gap = new_gap
    # Should converge close to equal — 50 K=32 ties drives well past tolerance.
    assert gap < 50.0
