"""Unit tests for utils.pair_selection (pure logic, no DB/ML)."""
import pytest

from utils.pair_selection import (
    InsufficientItemsError,
    select_exploration_pair,
    select_exploitation_pair,
    select_pair,
)


class Item:
    def __init__(self, id):
        self.id = id

    def __repr__(self):
        return f"Item({self.id})"


class Rating:
    def __init__(self, mu, sigma):
        self.mu = mu
        self.sigma = sigma


def ids(pair):
    return {pair[0].id, pair[1].id}


# --- select_exploration_pair --------------------------------------------------

def test_exploration_requires_two_items():
    with pytest.raises(InsufficientItemsError):
        select_exploration_pair([Item(1)], set())


def test_exploration_returns_valid_pair():
    items = [Item(1), Item(2), Item(3)]
    a, b = select_exploration_pair(items, set())
    assert a.id != b.id
    assert {a.id, b.id} <= {1, 2, 3}


def test_exploration_avoids_recent_pairs():
    items = [Item(1), Item(2)]
    # (1,2) is recent → only pair available is the recent one → falls back to full set
    a, b = select_exploration_pair(items, {(1, 2)})
    assert ids((a, b)) == {1, 2}


def test_exploration_picks_non_recent_when_possible(monkeypatch):
    items = [Item(1), Item(2), Item(3)]
    # Mark (1,2) and (1,3) recent → only (2,3) is fresh.
    a, b = select_exploration_pair(items, {(1, 2), (1, 3)})
    assert ids((a, b)) == {2, 3}


# --- select_exploitation_pair -------------------------------------------------

def test_exploitation_requires_two_items():
    with pytest.raises(InsufficientItemsError):
        select_exploitation_pair([Item(1)], {}, set())


def test_exploitation_random_fallback_when_few_ratings():
    items = [Item(1), Item(2), Item(3)]
    ratings = {1: Rating(25, 8)}  # only one rated → fallback to random.sample
    a, b = select_exploitation_pair(items, ratings, set())
    assert a.id != b.id


def test_exploitation_prefers_high_sigma_similar_mu():
    items = [Item(1), Item(2), Item(3)]
    ratings = {
        1: Rating(mu=25, sigma=9),
        2: Rating(mu=25, sigma=9),   # high sigma + same mu → best score
        3: Rating(mu=10, sigma=1),
    }
    a, b = select_exploitation_pair(items, ratings, set())
    assert ids((a, b)) == {1, 2}


def test_exploitation_fallback_when_all_candidates_recent():
    items = [Item(1), Item(2)]
    ratings = {1: Rating(25, 9), 2: Rating(25, 9)}
    a, b = select_exploitation_pair(items, ratings, {(1, 2)})
    assert ids((a, b)) == {1, 2}  # random fallback still returns the only pair


# --- select_pair --------------------------------------------------------------

def test_select_pair_requires_two_items():
    with pytest.raises(InsufficientItemsError):
        select_pair([Item(1)], {}, 0, set())


def test_select_pair_exploration_phase():
    items = [Item(1), Item(2), Item(3)]
    a, b = select_pair(items, {}, total_comparisons=0, recent_pairs=set())
    assert a.id != b.id


def test_select_pair_exploitation_phase():
    items = [Item(1), Item(2), Item(3)]
    ratings = {i.id: Rating(25, 8) for i in items}
    a, b = select_pair(items, ratings, total_comparisons=10_000, recent_pairs=set())
    assert a.id != b.id


def test_select_pair_randomize_order(monkeypatch):
    items = [Item(1), Item(2)]
    monkeypatch.setattr("utils.pair_selection.random.random", lambda: 0.99)  # > 0.5 → swap
    a, b = select_pair(items, {}, 0, set(), randomize_order=True)
    assert ids((a, b)) == {1, 2}
