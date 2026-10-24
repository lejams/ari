"""Metrics as ratios of summed counts, and their bootstrap intervals over items.

Every scorer reports raw counts per item (true positives, leaked turns, ...). A metric is a
ratio of sums of counts, so it can be recomputed on any subset of items: per segment, and on
bootstrap resamples. A ratio with an empty denominator is None (not measured), never zero.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

Counts = Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Ratio:
    name: str
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]

    def value(self, counts: Counts) -> float | None:
        denominator = sum(counts.get(key, 0.0) for key in self.denominator)
        if denominator <= 0:
            return None
        return sum(counts.get(key, 0.0) for key in self.numerator) / denominator


def add_counts(target: dict[str, float], counts: Counts) -> None:
    for key, value in counts.items():
        target[key] = target.get(key, 0.0) + value


def total(items: Iterable[Counts]) -> dict[str, float]:
    summed: dict[str, float] = {}
    for counts in items:
        add_counts(summed, counts)
    return summed


def suffixes(keys: Iterable[str], prefix: str) -> list[str]:
    """Sorted distinct `x` of the keys shaped `<prefix>.x`."""
    return sorted({key[len(prefix) + 1 :] for key in keys if key.startswith(prefix + ".")})


def precision_recall(name: str, prefix: str, label: str) -> list[Ratio]:
    """Precision and recall from `<prefix>.tp|fp|fn.<label>` counts."""
    tp, fp, fn = (f"{prefix}.{kind}.{label}" for kind in ("tp", "fp", "fn"))
    return [
        Ratio(f"{name}.precision.{label}", (tp,), (tp, fp)),
        Ratio(f"{name}.recall.{label}", (tp,), (tp, fn)),
    ]


# Shared by every suite: usable structured output after one retry, and verdict stability.
COMMON_RATIOS = (
    Ratio("structured_after_retry", ("runs.usable",), ("runs.total",)),
    Ratio("structured_first_try", ("runs.first_try",), ("runs.total",)),
    Ratio("stability.changed_rate", ("stability.changed",), ("stability.items",)),
)


def compute(ratios: Sequence[Ratio], counts: Counts) -> dict[str, float | None]:
    return {ratio.name: ratio.value(counts) for ratio in ratios}


def bootstrap_interval(
    ratio: Ratio,
    per_item: Sequence[Counts],
    *,
    resamples: int = 1000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float] | None:
    """Percentile interval of the metric over items resampled with replacement.

    An item carries all its repetitions, so the interval reflects both the variation between
    items and between repetitions. Seeded: the same report always gives the same interval.
    """
    if not per_item or ratio.value(total(per_item)) is None:
        return None
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        sample = [per_item[rng.randrange(len(per_item))] for _ in per_item]
        value = ratio.value(total(sample))
        if value is not None:
            values.append(value)
    return _percentiles(values, level)


def paired_difference_interval(
    ratio: Ratio,
    baseline: Sequence[Counts],
    candidate: Sequence[Counts],
    *,
    resamples: int = 1000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float] | None:
    """Interval of candidate minus baseline, resampling the same items for both (paired)."""
    if len(baseline) != len(candidate) or not baseline:
        return None
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        indices = [rng.randrange(len(baseline)) for _ in baseline]
        before = ratio.value(total(baseline[i] for i in indices))
        after = ratio.value(total(candidate[i] for i in indices))
        if before is not None and after is not None:
            values.append(after - before)
    return _percentiles(values, level)


def _percentiles(values: list[float], level: float) -> tuple[float, float] | None:
    if not values:
        return None
    values.sort()
    tail = (1 - level) / 2
    low = values[int(tail * (len(values) - 1))]
    high = values[round((1 - tail) * (len(values) - 1))]
    return round(low, 6), round(high, 6)
