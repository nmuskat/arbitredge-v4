"""
validation/harness/experiment_log.py

Writes one row to validation_experiments per comparison. Every field the
schema defines is accepted explicitly (no silent defaults for the
statistically important columns), so a caller can't accidentally omit
something correction 3 specifically asked to be recorded.
"""

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def log_experiment(
    conn,
    experiment_name, target_name, target_type, evidence_family,
    baseline_feature_set, candidate_feature_set, walk_forward_config,
    n_folds, n_train_total, n_validation_total,
    metric_name, baseline_metric_value, candidate_metric_value, marginal_improvement,
    statistical_test_used, raw_p_value, ci_lower, ci_upper,
    fdr_family_id=None, fdr_adjusted_p_value=None, fdr_adjusted_significant=None,
    holdout_key=None, holdout_touched=0, passed_holdout_confirmation=None,
    insufficient_sample=0, dependence_warning=0,
    non_behavioral_warning=None, notes=None,
) -> int:
    cur = conn.execute(
        "INSERT INTO validation_experiments ("
        "experiment_name, run_at, target_name, target_type, evidence_family, "
        "baseline_feature_set, candidate_feature_set, walk_forward_config, "
        "n_folds, n_train_total, n_validation_total, "
        "holdout_key, holdout_touched, "
        "metric_name, baseline_metric_value, candidate_metric_value, marginal_improvement, "
        "statistical_test_used, raw_p_value, ci_lower, ci_upper, "
        "fdr_family_id, fdr_adjusted_p_value, fdr_adjusted_significant, "
        "passed_holdout_confirmation, insufficient_sample, dependence_warning, "
        "non_behavioral_warning, notes"
        ") VALUES (?,?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?)",
        (
            experiment_name, _now(), target_name, target_type, evidence_family,
            baseline_feature_set, candidate_feature_set, walk_forward_config,
            n_folds, n_train_total, n_validation_total,
            holdout_key, holdout_touched,
            metric_name, baseline_metric_value, candidate_metric_value, marginal_improvement,
            statistical_test_used, raw_p_value, ci_lower, ci_upper,
            fdr_family_id, fdr_adjusted_p_value, fdr_adjusted_significant,
            passed_holdout_confirmation, insufficient_sample, dependence_warning,
            non_behavioral_warning, notes,
        ),
    )
    conn.commit()
    return cur.lastrowid
