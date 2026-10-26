"""
validation/harness/splitting.py

Chronological, expanding-window walk-forward folds. Deliberately does NOT
trust any external "week number" label (ESPN's week_number can be noisy,
especially for CFB) -- folds are derived purely from sorting games by their
true commence_time and slicing sequentially, which makes chronological
correctness a structural guarantee, not an assumption. Every fold's train
set is entirely before its validation set in real time, by construction.
"""

from datetime import datetime


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _adjust_cursor_past_ties(sorted_games, cursor):
    """Never let a fold boundary fall in the middle of a group of games
    sharing an identical commence_time (routine in reality -- e.g. every
    early-window NFL Sunday game kicks off simultaneously). Pushes the
    cursor forward past any tied timestamp so a boundary only ever falls
    between two DIFFERENT timestamps."""
    if cursor <= 0 or cursor >= len(sorted_games):
        return cursor
    boundary_time = sorted_games[cursor - 1][1]
    while cursor < len(sorted_games) and sorted_games[cursor][1] == boundary_time:
        cursor += 1
    return cursor


def generate_walk_forward_folds(games: list, n_folds: int = 5, min_train_size: int = 20):
    """
    games: list of (canonical_game_id, commence_time) tuples, any order.
    Returns a list of dicts: {fold_label, train_ids, validation_ids,
    train_max_commence_time, validation_min_commence_time}.

    Expanding window: fold i's train set includes ALL games before fold i's
    validation window (not just the immediately preceding fold), which is
    the standard walk-forward definition and maximizes usable training data
    as the walk progresses.

    Fold boundaries are adjusted to never split a group of games sharing an
    identical commence_time (see _adjust_cursor_past_ties) -- simultaneous
    kickoffs are routine in real schedules and must never end up split
    across train/validation for the same boundary.

    Returns an empty list (not an error) if there isn't enough data for at
    least one fold beyond min_train_size -- the caller is responsible for
    reporting this as an insufficient-sample condition, not this function.
    """
    sorted_games = sorted(games, key=lambda g: g[1])
    total = len(sorted_games)

    if total <= min_train_size:
        return []

    min_train_size = _adjust_cursor_past_ties(sorted_games, min_train_size)
    if min_train_size >= total:
        return []

    remaining = total - min_train_size
    effective_n_folds = min(n_folds, remaining)
    if effective_n_folds < 1:
        return []

    fold_size = remaining // effective_n_folds
    if fold_size < 1:
        fold_size = 1

    folds = []
    cursor = min_train_size
    for i in range(effective_n_folds):
        is_last = (i == effective_n_folds - 1)
        val_end = total if is_last else min(cursor + fold_size, total)
        val_end = _adjust_cursor_past_ties(sorted_games, val_end)
        if cursor >= val_end:
            if is_last:
                val_end = total
            else:
                continue

        train_slice = sorted_games[:cursor]
        val_slice = sorted_games[cursor:val_end]
        if not val_slice:
            continue

        train_ids = [g[0] for g in train_slice]
        val_ids = [g[0] for g in val_slice]

        train_max_time = train_slice[-1][1]
        val_min_time = val_slice[0][1]

        assert train_max_time < val_min_time, (
            f"Chronological ordering violated in fold {i+1}: "
            f"train_max={train_max_time} val_min={val_min_time}"
        )

        folds.append({
            "fold_label": f"fold_{i + 1}",
            "train_ids": train_ids,
            "validation_ids": val_ids,
            "train_max_commence_time": train_max_time,
            "validation_min_commence_time": val_min_time,
        })
        cursor = val_end

    return folds
