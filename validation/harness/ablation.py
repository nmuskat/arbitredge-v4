"""
validation/harness/ablation.py

Nested ablation: baseline feature set vs. baseline+candidate feature set,
same target, same folds, same metric. Fits a simple, well-tested model
(logistic regression for classification, ridge regression for regression)
per fold -- this is measurement-only tooling to test whether a feature set
carries signal, NOT a production scoring model, and produces no stored
weight of any kind (per principle 10).

FAIRNESS DESIGN DECISION: both the baseline and candidate models for a
given fold are evaluated on the SAME complete-case subset of games (using
the candidate feature set's -- the superset's -- completeness, since it
has strictly more columns and therefore more chances for a NULL). Without
this, a candidate model could look artificially worse (or better) simply
from being fit/evaluated on a different, smaller set of games than the
baseline, confounding the comparison. This is a deliberate, disclosed
methodology choice.

Missing values are NEVER imputed. A row with any None among the required
columns for a given comparison is excluded from that fold's fit/eval
entirely (complete-case only), consistent with the project's standing
rule against fabricating data.
"""

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge

from validation.harness.metrics import (
    DEFAULT_METRIC_BY_FAMILY, compute_classification_metric,
    compute_regression_metric, compute_multiclass_metric,
)

MIN_TRAIN_ROWS = 5
MIN_VALIDATION_ROWS = 3


def _complete_case_ids(conn, feature_set, target, game_ids):
    usable = []
    for gid in game_ids:
        feats = feature_set.get_features(conn, gid, as_of_cutoff=None)
        if feats is None or any(v is None for v in feats.values()):
            continue

        # Defensive cross-check: the engineered_features row's own recorded
        # point_in_time_cutoff must match this game's REAL commence_time.
        # A mismatch would mean the feature row was computed against a
        # different cutoff than this game's actual kickoff -- a data
        # corruption/leakage scenario -- and the game is excluded rather
        # than silently trusted.
        cutoff_used = feature_set.point_in_time_cutoff_used(conn, gid)
        real_commence_time = conn.execute(
            "SELECT commence_time FROM games_canonical WHERE canonical_game_id = ?", (gid,)
        ).fetchone()
        if real_commence_time is None or cutoff_used != real_commence_time[0]:
            continue

        label = target.get_label(conn, gid, as_of_cutoff=None)
        if label is None:
            continue
        usable.append(gid)
    return usable


def _build_matrix(conn, feature_set, columns, game_ids, fit_categories=None):
    rows = [feature_set.get_features(conn, gid, as_of_cutoff=None) for gid in game_ids]
    numeric_cols = [c for c in columns if not isinstance(rows[0][c], str)] if rows else columns
    categorical_cols = [c for c in columns if c not in numeric_cols]

    if fit_categories is None:
        fit_categories = {c: sorted({r[c] for r in rows}) for c in categorical_cols}

    matrix = []
    for r in rows:
        vec = [float(r[c]) for c in numeric_cols]
        for c in categorical_cols:
            vec.extend([1.0 if r[c] == cat else 0.0 for cat in fit_categories[c]])
        matrix.append(vec)
    return np.array(matrix), fit_categories


def _fit_predict(metric_family, X_train, y_train, X_val):
    if metric_family == "classification":
        if len(set(y_train)) < 2:
            return None  # cannot fit -- only one class present in training labels
        model = LogisticRegression(max_iter=2000, C=1.0)
        model.fit(X_train, y_train)
        return model.predict_proba(X_val)[:, 1]
    if metric_family == "regression":
        model = Ridge(alpha=1.0)
        model.fit(X_train, y_train)
        return model.predict(X_val)
    raise NotImplementedError(f"metric_family {metric_family!r} model fitting not yet implemented")


def run_ablation(conn, target, baseline_feature_set, candidate_feature_set, baseline_columns, candidate_columns, folds, metric_name=None):
    """
    Returns (fold_results, summary) where fold_results is a list of dicts
    per fold (baseline_metric, candidate_metric, signed_diff, n_train, n_val,
    or an 'insufficient' flag), and summary aggregates counts.
    """
    metric_family = target.metric_family
    if metric_name is None:
        metric_name = DEFAULT_METRIC_BY_FAMILY[metric_family]

    fold_results = []

    for fold in folds:
        train_ids = _complete_case_ids(conn, candidate_feature_set, target, fold["train_ids"])
        val_ids = _complete_case_ids(conn, candidate_feature_set, target, fold["validation_ids"])

        if len(train_ids) < MIN_TRAIN_ROWS or len(val_ids) < MIN_VALIDATION_ROWS:
            fold_results.append({
                "fold_label": fold["fold_label"], "insufficient": True,
                "n_train": len(train_ids), "n_validation": len(val_ids),
            })
            continue

        y_train = [target.get_label(conn, gid, as_of_cutoff=None) for gid in train_ids]
        y_val = [target.get_label(conn, gid, as_of_cutoff=None) for gid in val_ids]

        X_train_base, cats = _build_matrix(conn, baseline_feature_set, baseline_columns, train_ids)
        X_val_base, _ = _build_matrix(conn, baseline_feature_set, baseline_columns, val_ids, fit_categories=cats)

        X_train_cand, cats2 = _build_matrix(conn, candidate_feature_set, candidate_columns, train_ids)
        X_val_cand, _ = _build_matrix(conn, candidate_feature_set, candidate_columns, val_ids, fit_categories=cats2)

        baseline_pred = _fit_predict(metric_family, X_train_base, y_train, X_val_base)
        candidate_pred = _fit_predict(metric_family, X_train_cand, y_train, X_val_cand)

        if baseline_pred is None or candidate_pred is None:
            fold_results.append({
                "fold_label": fold["fold_label"], "insufficient": True,
                "n_train": len(train_ids), "n_validation": len(val_ids),
            })
            continue

        if metric_family == "classification":
            baseline_metric = compute_classification_metric(y_val, baseline_pred, metric_name)
            candidate_metric = compute_classification_metric(y_val, candidate_pred, metric_name)
        elif metric_family == "regression":
            baseline_metric = compute_regression_metric(y_val, baseline_pred, metric_name)
            candidate_metric = compute_regression_metric(y_val, candidate_pred, metric_name)
        else:
            baseline_metric = compute_multiclass_metric(y_val, baseline_pred, metric_name)
            candidate_metric = compute_multiclass_metric(y_val, candidate_pred, metric_name)

        fold_results.append({
            "fold_label": fold["fold_label"], "insufficient": False,
            "n_train": len(train_ids), "n_validation": len(val_ids),
            "baseline_metric": baseline_metric, "candidate_metric": candidate_metric,
        })

    valid_folds = [f for f in fold_results if not f["insufficient"]]
    summary = {
        "metric_name": metric_name,
        "n_folds_total": len(folds),
        "n_folds_usable": len(valid_folds),
        "n_train_total": sum(f["n_train"] for f in fold_results),
        "n_validation_total": sum(f["n_validation"] for f in fold_results),
    }
    return fold_results, summary
