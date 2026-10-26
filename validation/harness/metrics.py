"""
validation/harness/metrics.py

Per-target-type metrics. HIGHER_IS_BETTER records the direction for each
metric so marginal_improvement is always computed as "positive means the
candidate feature set did better," regardless of whether the underlying
metric itself is higher-is-better (AUC, accuracy) or lower-is-better
(MAE, RMSE, log-loss).
"""

from sklearn.metrics import roc_auc_score, accuracy_score, mean_absolute_error, log_loss

DEFAULT_METRIC_BY_FAMILY = {
    "classification": "auc",
    "regression": "mae",
    "multiclass": "accuracy",
}

HIGHER_IS_BETTER = {
    "auc": True,
    "accuracy": True,
    "mae": False,
    "rmse": False,
    "log_loss": False,
}


def compute_classification_metric(y_true, y_pred_proba, metric_name="auc"):
    if len(set(y_true)) < 2:
        return None  # metric undefined -- validation fold has only one outcome class present
    if metric_name == "auc":
        return float(roc_auc_score(y_true, y_pred_proba))
    if metric_name == "accuracy":
        preds = [1 if p >= 0.5 else 0 for p in y_pred_proba]
        return float(accuracy_score(y_true, preds))
    if metric_name == "log_loss":
        try:
            return float(log_loss(y_true, y_pred_proba, labels=[0, 1]))
        except ValueError:
            return None
    raise ValueError(f"Unknown classification metric: {metric_name}")


def compute_regression_metric(y_true, y_pred, metric_name="mae"):
    if metric_name == "mae":
        return float(mean_absolute_error(y_true, y_pred))
    if metric_name == "rmse":
        errors = [(a - b) ** 2 for a, b in zip(y_true, y_pred)]
        return float((sum(errors) / len(errors)) ** 0.5)
    raise ValueError(f"Unknown regression metric: {metric_name}")


def compute_multiclass_metric(y_true, y_pred, metric_name="accuracy"):
    if metric_name == "accuracy":
        return float(accuracy_score(y_true, y_pred))
    raise ValueError(f"Unknown multiclass metric: {metric_name}")


def signed_improvement(baseline_value, candidate_value, metric_name):
    """Returns (candidate - baseline) or (baseline - candidate) depending on
    metric direction, so a positive result always means 'candidate improved.'
    Returns None if either input is None."""
    if baseline_value is None or candidate_value is None:
        return None
    if HIGHER_IS_BETTER.get(metric_name, True):
        return candidate_value - baseline_value
    return baseline_value - candidate_value
