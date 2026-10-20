"""The Beta distribution in pure Python: mean, cumulative distribution and quantiles.

The learning core may import nothing but the standard library, so there is no scipy. The
cumulative distribution is the regularised incomplete beta function, computed with the
continued fraction of Lentz's method (Numerical Recipes, section 6.4); quantiles invert it by
bisection, which is slow for a library but exact enough (about 1e-15) and always converges
because the distribution function is monotone. A mastery estimate needs two quantiles per
skill, so the cost is negligible.

Tests check the implementation against closed forms: Beta(1, b) has cdf 1 - (1 - x)^b,
Beta(a, 1) has cdf x^a, and Beta(a, a) is symmetric.
"""

from __future__ import annotations

from math import exp, lgamma, log

_MAX_ITERATIONS = 400
_EPSILON = 1e-15
_TINY = 1e-300


def beta_mean(a: float, b: float) -> float:
    return a / (a + b)


def _continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's evaluation of the continued fraction of the incomplete beta function."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, _MAX_ITERATIONS + 1):
        m2 = 2 * m
        step = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + step * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + step / c
        c = c if abs(c) > _TINY else _TINY
        h *= d * c
        step = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + step * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + step / c
        c = c if abs(c) > _TINY else _TINY
        change = d * c
        h *= change
        if abs(change - 1.0) < _EPSILON:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """P(X <= x) for X ~ Beta(a, b), with a, b > 0."""
    if a <= 0 or b <= 0:
        raise ValueError("Beta parameters must be positive")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1.0 - x))
    # The continued fraction converges fast below the mean; use the symmetry above it.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _continued_fraction(a, b, x) / a
    return 1.0 - front * _continued_fraction(b, a, 1.0 - x) / b


def beta_ppf(p: float, a: float, b: float) -> float:
    """The quantile of order p for Beta(a, b): the x such that P(X <= x) = p."""
    if not 0.0 <= p <= 1.0:
        raise ValueError("A quantile order lies in [0, 1]")
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if beta_cdf(middle, a, b) < p:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0
