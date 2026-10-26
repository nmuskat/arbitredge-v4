"""
validation/harness/run_experiment.py

Orchestrates one baseline-vs-candidate comparison: excludes any locked
holdout from the rolling cohort, generates walk-forward folds, runs
ablation, evaluates paired significance, and logs one row to
validation_experiments. For target_type='diagnostic', prints
DIAGNOSTIC_BANNER before AND after the result -- per explicit instruction,
a 2025 diagnostic result must be visually impossible to mistake for
behavioral evidence.
"""

import json

from validation.harness.splitting import generate_walk_forward_folds
from validation.harness.ablation import run_ablation
from validation.harness.statistics import evaluate_paired_comparison
from validation.harness.experiment_log import log_experiment
from validation.harness.holdout import filter_out_holdout_games
from validation.harness.metrics import signed_improvement
from validation.targets.base import NON_BEHAVIORAL_WARNING

DIAGNOSTIC_BANNER = (
    "\n" + ("!" * 70) + "\n"
    + "!!  " + NON_BEHAVIORAL_WARNING + "\n"
    + ("!" * 70) + "\n"
)


def run_single_comparison(
    conn, experiment_name, target, baseline_feature_set, candidate_feature_set,
    baseline_columns, candidate_columns, cohort_game_ids_with_time,
    holdout_key=None, n_folds=5,
):
    is_diagnostic = target.target_type == "diagnostic"
    if is_diagnostic:
        print(DIAGNOSTIC_BANNER)

    if holdout_key:
        allowed = set(filter_out_holdout_games(conn, holdout_key, [g for g, _ in cohort_game_ids_with_time]))
        cohort_for_folds = [(g, t) for g, t in cohort_game_ids_with_time if g in allowed]
    else:
        cohort_for_folds = list(cohort_game_ids_with_time)

    folds = generate_walk_forward_folds(cohort_for_folds, n_folds=n_folds)
    walk_forward_config = json.dumps({
        "n_folds_requested": n_folds,
        "n_folds_generated": len(folds),
        "fold_labels": [f["fold_label"] for f in folds],
        "holdout_key_excluded": holdout_key,
    })

    if not folds:
        experiment_id = log_experiment(
            conn, experiment_name=experiment_name, target_name=target.name, target_type=target.target_type,
            evidence_family=target.evidence_family, baseline_feature_set=baseline_feature_set.rung_name,
            candidate_feature_set=candidate_feature_set.rung_name, walk_forward_config=walk_forward_config,
            n_folds=0, n_train_total=0, n_validation_total=0, metric_name="",
            baseline_metric_value=None, candidate_metric_value=None, marginal_improvement=None,
            statistical_test_used="insufficient_sample", raw_p_value=None, ci_lower=None, ci_upper=None,
            insufficient_sample=1, dependence_warning=1,
            non_behavioral_warning=(NON_BEHAVIORAL_WARNING if is_diagnostic else None),
            notes="No usable folds generated -- cohort too small for the configured min_train_size/n_folds.",
        )
        result = {"experiment_id": experiment_id, "insufficient_sample": True}
        if is_diagnostic:
            print(f"RESULT: INSUFFICIENT SAMPLE (experiment_id={experiment_id})")
            print(DIAGNOSTIC_BANNER)
        return result

    fold_results, summary = run_ablation(
        conn, target, baseline_feature_set, candidate_feature_set,
        baseline_columns, candidate_columns, folds,
    )

    usable = [f for f in fold_results if not f["insufficient"]]
    diffs = []
    baseline_values, candidate_values = [], []
    for f in usable:
        d = signed_improvement(f["baseline_metric"], f["candidate_metric"], summary["metric_name"])
        if d is not None:
            diffs.append(d)
            baseline_values.append(f["baseline_metric"])
            candidate_values.append(f["candidate_metric"])

    if diffs:
        stats_result = evaluate_paired_comparison(diffs)
    else:
        stats_result = {
            "statistical_test_used": "insufficient_sample", "raw_p_value": None,
            "ci_lower": None, "ci_upper": None, "insufficient_sample": True,
            "dependence_warning": True, "crosscheck_notes": "",
        }

    baseline_mean = sum(baseline_values) / len(baseline_values) if baseline_values else None
    candidate_mean = sum(candidate_values) / len(candidate_values) if candidate_values else None
    marginal_improvement = sum(diffs) / len(diffs) if diffs else None

    experiment_id = log_experiment(
        conn, experiment_name=experiment_name, target_name=target.name, target_type=target.target_type,
        evidence_family=target.evidence_family, baseline_feature_set=baseline_feature_set.rung_name,
        candidate_feature_set=candidate_feature_set.rung_name, walk_forward_config=walk_forward_config,
        n_folds=len(folds), n_train_total=summary["n_train_total"], n_validation_total=summary["n_validation_total"],
        metric_name=summary["metric_name"], baseline_metric_value=baseline_mean,
        candidate_metric_value=candidate_mean, marginal_improvement=marginal_improvement,
        statistical_test_used=stats_result["statistical_test_used"], raw_p_value=stats_result["raw_p_value"],
        ci_lower=stats_result["ci_lower"], ci_upper=stats_result["ci_upper"],
        insufficient_sample=1 if stats_result["insufficient_sample"] else 0,
        dependence_warning=1 if stats_result["dependence_warning"] else 0,
        non_behavioral_warning=(NON_BEHAVIORAL_WARNING if is_diagnostic else None),
        notes=stats_result.get("crosscheck_notes", ""),
    )

    result = {
        "experiment_id": experiment_id,
        "insufficient_sample": stats_result["insufficient_sample"],
        "n_folds_usable": len(usable),
        "metric_name": summary["metric_name"],
        "baseline_metric_value": baseline_mean,
        "candidate_metric_value": candidate_mean,
        "marginal_improvement": marginal_improvement,
        "raw_p_value": stats_result["raw_p_value"],
        "ci_lower": stats_result["ci_lower"],
        "ci_upper": stats_result["ci_upper"],
        "statistical_test_used": stats_result["statistical_test_used"],
    }

    if is_diagnostic:
        print(f"RESULT (experiment_id={experiment_id}): metric={summary['metric_name']} "
              f"baseline={baseline_mean} candidate={candidate_mean} "
              f"marginal_improvement={marginal_improvement} p={stats_result['raw_p_value']} "
              f"test={stats_result['statistical_test_used']}")
        print("This number describes correlation with GAME OUTCOMES ONLY.")
        print(DIAGNOSTIC_BANNER)

    return result
