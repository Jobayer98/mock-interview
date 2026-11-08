"""Agreement and consistency metrics for the evaluator (success metrics 1 and 2 in DESIGN.md)."""
from __future__ import annotations

from collections import Counter
from statistics import mean


def exact_match(pred: list[int], gold: list[int]) -> float:
    return mean(p == g for p, g in zip(pred, gold))


def within_one(pred: list[int], gold: list[int]) -> float:
    return mean(abs(p - g) <= 1 for p, g in zip(pred, gold))


def mean_abs_error(pred: list[int], gold: list[int]) -> float:
    return mean(abs(p - g) for p, g in zip(pred, gold))


def quadratic_weighted_kappa(pred: list[int], gold: list[int], levels: int = 4) -> float:
    """QWK with scores 1..levels. 1.0 = perfect agreement, 0 = chance, <0 = worse than chance."""
    n = len(pred)
    observed = [[0.0] * levels for _ in range(levels)]
    for p, g in zip(pred, gold):
        observed[g - 1][p - 1] += 1
    hist_gold = Counter(gold)
    hist_pred = Counter(pred)
    num = den = 0.0
    for i in range(levels):
        for j in range(levels):
            weight = (i - j) ** 2 / (levels - 1) ** 2
            expected = hist_gold.get(i + 1, 0) * hist_pred.get(j + 1, 0) / n
            num += weight * observed[i][j]
            den += weight * expected
    return 1.0 if den == 0 else 1.0 - num / den


def confusion(pred: list[int], gold: list[int], levels: int = 4) -> list[list[int]]:
    """Rows = human label, columns = model score."""
    grid = [[0] * levels for _ in range(levels)]
    for p, g in zip(pred, gold):
        grid[g - 1][p - 1] += 1
    return grid


def consistency(runs_by_item: list[list[int]]) -> dict[str, float]:
    """runs_by_item[i] = scores from repeated runs of item i. Needs >= 2 runs per item."""
    usable = [r for r in runs_by_item if len(r) >= 2]
    if not usable:
        return {"identical": float("nan"), "within_one": float("nan")}
    return {
        "identical": mean(len(set(r)) == 1 for r in usable),
        "within_one": mean(max(r) - min(r) <= 1 for r in usable),
    }
