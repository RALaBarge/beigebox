"""
Statistical validation for discovery experiments.

Provides Welch's t-test (unequal variance), Cohen's d effect size,
and Bonferroni correction for multiple comparisons.

Usage
-----
    from beigebox.eval.stats import significance_test, SignificanceResult

    result = significance_test(baseline_scores, challenger_scores)
    if result.significant and result.cohens_d > 0.2:
        # Champion beats baseline with practical effect
        ...
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SignificanceResult:
    """Output of a significance test."""

    significant: bool       # p < alpha after Bonferroni correction
    p_value: float          # raw p-value (one-tailed Welch's t-test)
    t_statistic: float
    cohens_d: float         # effect size (0.2=small, 0.5=medium, 0.8=large)
    n_baseline: int
    n_challenger: int
    mean_baseline: float
    mean_challenger: float
    delta: float            # mean_challenger - mean_baseline
    alpha: float            # corrected alpha used
    verdict: str            # human-readable summary


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _welch_t(
    mean1: float, var1: float, n1: int,
    mean2: float, var2: float, n2: int,
) -> tuple[float, float]:
    """Welch's t-statistic and degrees of freedom (Welch-Satterthwaite)."""
    se1 = var1 / n1 if n1 > 0 else 0.0
    se2 = var2 / n2 if n2 > 0 else 0.0
    se_total = se1 + se2

    if se_total == 0:
        return 0.0, 1.0

    t = (mean2 - mean1) / math.sqrt(se_total)

    # Welch-Satterthwaite degrees of freedom
    numerator = se_total ** 2
    denominator = (se1 ** 2 / (n1 - 1) if n1 > 1 else 0) + (se2 ** 2 / (n2 - 1) if n2 > 1 else 0)
    df = numerator / denominator if denominator > 0 else 1.0

    return t, df


def _t_cdf_approx(t: float, df: float) -> float:
    """
    Approximation of the one-tailed p-value for t-distribution.

    Uses the regularised incomplete beta function approximation for df > 1.
    Accurate to ~2 decimal places — sufficient for experiment decisions.
    """
    # Large df → normal approximation
    if df > 100:
        # One-tailed p via standard normal approximation
        z = abs(t)
        # Abramowitz & Stegun approximation for standard normal CDF
        p1 = 1.0 / (1 + 0.2316419 * z)
        poly = p1 * (0.319381530
                     + p1 * (-0.356563782
                             + p1 * (1.781477937
                                     + p1 * (-1.821255978
                                             + p1 * 1.330274429))))
        phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
        one_tail = phi * poly
        return one_tail if t > 0 else 1.0 - one_tail

    # Incomplete beta approximation via continued fraction (Lentz method)
    x = df / (df + t * t)
    # regularized incomplete beta B(x; df/2, 0.5) / B(df/2, 0.5)
    # Approximated with simple series
    try:
        half_df = df / 2.0
        log_beta = (math.lgamma(half_df) + math.lgamma(0.5) - math.lgamma(half_df + 0.5))

        # Use Newton's approximation for the regularized incomplete beta
        # Good enough for hypothesis testing at typical sample sizes
        a, b = half_df, 0.5
        if x == 0:
            ibeta = 0.0
        elif x == 1:
            ibeta = 1.0
        else:
            # Simple power series approximation
            ibeta = 0.0
            term = math.exp(a * math.log(x) + b * math.log(1 - x) - log_beta) / a
            for i in range(200):
                ibeta += term
                term *= (a + i) * x / (a + b + i)
                if abs(term) < 1e-10:
                    break

        two_tail_p = min(1.0, max(0.0, ibeta))
        one_tail_p = two_tail_p / 2.0
        return one_tail_p if t > 0 else 1.0 - one_tail_p
    except Exception:
        # Fallback: conservative p=0.5
        return 0.5


def significance_test(
    baseline: list[float],
    challenger: list[float],
    alpha: float = 0.05,
    n_comparisons: int = 1,
) -> SignificanceResult:
    """
    One-tailed Welch's t-test: does challenger beat baseline?

    Parameters
    ----------
    baseline:
        Score samples for the control/baseline variant.
    challenger:
        Score samples for the challenger variant.
    alpha:
        Significance level before Bonferroni correction (default 0.05).
    n_comparisons:
        Number of simultaneous comparisons for Bonferroni correction.
        Pass len(variants) - 1 when comparing multiple challengers to one baseline.

    Returns
    -------
    SignificanceResult with verdict and all statistics.
    """
    if not baseline or not challenger:
        return SignificanceResult(
            significant=False, p_value=1.0, t_statistic=0.0, cohens_d=0.0,
            n_baseline=len(baseline), n_challenger=len(challenger),
            mean_baseline=_mean(baseline), mean_challenger=_mean(challenger),
            delta=0.0, alpha=alpha, verdict="insufficient data",
        )

    m1, m2 = _mean(baseline), _mean(challenger)
    v1, v2 = _variance(baseline), _variance(challenger)
    n1, n2 = len(baseline), len(challenger)

    t, df = _welch_t(m1, v1, n1, m2, v2, n2)
    p = _t_cdf_approx(t, df)

    # Cohen's d (pooled SD)
    pooled_sd = math.sqrt((v1 + v2) / 2) if (v1 + v2) > 0 else 0.001
    d = (m2 - m1) / pooled_sd

    # Bonferroni-corrected alpha
    corrected_alpha = alpha / max(1, n_comparisons)
    significant = (p < corrected_alpha) and (d > 0)

    if significant and d >= 0.8:
        verdict = f"✅ Strong win: Δ={m2-m1:+.3f}, d={d:.2f}, p={p:.4f}"
    elif significant and d >= 0.2:
        verdict = f"✅ Win: Δ={m2-m1:+.3f}, d={d:.2f}, p={p:.4f}"
    elif significant:
        verdict = f"⚠️ Marginal win (small effect): Δ={m2-m1:+.3f}, d={d:.2f}, p={p:.4f}"
    elif m2 > m1:
        verdict = f"⬆ Trending (not significant): Δ={m2-m1:+.3f}, p={p:.4f}"
    elif m2 < m1:
        verdict = f"⬇ Worse than baseline: Δ={m2-m1:+.3f}, p={p:.4f}"
    else:
        verdict = f"= No difference: Δ={m2-m1:+.3f}"

    return SignificanceResult(
        significant=significant,
        p_value=p,
        t_statistic=t,
        cohens_d=d,
        n_baseline=n1,
        n_challenger=n2,
        mean_baseline=m1,
        mean_challenger=m2,
        delta=m2 - m1,
        alpha=corrected_alpha,
        verdict=verdict,
    )


def compare_all_to_baseline(
    baseline_name: str,
    scores_by_variant: dict[str, list[float]],
    alpha: float = 0.05,
) -> dict[str, SignificanceResult]:
    """
    Compare all variants to the named baseline with Bonferroni correction.

    Parameters
    ----------
    baseline_name:
        Key in scores_by_variant to treat as control.
    scores_by_variant:
        Dict mapping variant name → list of overall scores.
    alpha:
        Family-wise significance level.

    Returns
    -------
    Dict mapping challenger name → SignificanceResult.
    """
    baseline_scores = scores_by_variant.get(baseline_name, [])
    challengers = {k: v for k, v in scores_by_variant.items() if k != baseline_name}
    n_comparisons = len(challengers)

    return {
        name: significance_test(baseline_scores, scores, alpha=alpha, n_comparisons=n_comparisons)
        for name, scores in challengers.items()
    }
