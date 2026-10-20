"""The Beta distribution in pure Python, checked against closed forms (no scipy in the core)."""

from __future__ import annotations

import random

import pytest

from ari.learning.beta import beta_cdf, beta_mean, beta_ppf


@pytest.mark.parametrize("b", [1.0, 2.0, 3.5, 10.0, 80.0])
def test_beta_one_b_has_the_closed_form_cdf(b: float) -> None:
    # Beta(1, b): F(x) = 1 - (1 - x)^b
    for x in (0.001, 0.05, 0.3, 0.5, 0.9, 0.999):
        assert beta_cdf(x, 1.0, b) == pytest.approx(1 - (1 - x) ** b, abs=1e-12)


@pytest.mark.parametrize("a", [1.0, 2.0, 3.5, 10.0, 80.0])
def test_beta_a_one_has_the_closed_form_cdf(a: float) -> None:
    # Beta(a, 1): F(x) = x^a
    for x in (0.001, 0.05, 0.3, 0.5, 0.9, 0.999):
        assert beta_cdf(x, a, 1.0) == pytest.approx(x**a, abs=1e-12)


def test_the_uniform_distribution_is_the_identity() -> None:
    for p in (0.0, 0.1, 0.5, 0.9, 1.0):
        assert beta_ppf(p, 1.0, 1.0) == pytest.approx(p, abs=1e-12)


@pytest.mark.parametrize("a", [0.5, 1.0, 2.0, 7.0, 40.0])
def test_symmetric_beta_has_its_median_at_one_half_and_symmetric_quantiles(a: float) -> None:
    assert beta_ppf(0.5, a, a) == pytest.approx(0.5, abs=1e-10)
    assert beta_ppf(0.1, a, a) + beta_ppf(0.9, a, a) == pytest.approx(1.0, abs=1e-10)


def test_known_value_of_the_arcsine_distribution() -> None:
    # Beta(1/2, 1/2): F(x) = (2/pi) * asin(sqrt(x)); the median is 1/2, F(0.25) = 1/3.
    assert beta_cdf(0.25, 0.5, 0.5) == pytest.approx(1 / 3, abs=1e-12)


def test_cdf_and_ppf_are_inverse_and_monotone_on_random_parameters() -> None:
    rng = random.Random(20261019)
    for _ in range(200):
        a, b = rng.uniform(0.3, 60.0), rng.uniform(0.3, 60.0)
        quantiles = [beta_ppf(p, a, b) for p in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)]
        assert quantiles == sorted(quantiles)
        for p in (0.1, 0.5, 0.9):
            assert beta_cdf(beta_ppf(p, a, b), a, b) == pytest.approx(p, abs=1e-9)


def test_mean_and_invalid_arguments() -> None:
    assert beta_mean(3.0, 1.0) == 0.75
    with pytest.raises(ValueError):
        beta_cdf(0.5, 0.0, 1.0)
    with pytest.raises(ValueError):
        beta_ppf(1.5, 1.0, 1.0)
    assert beta_cdf(-1.0, 2.0, 2.0) == 0.0 and beta_cdf(2.0, 2.0, 2.0) == 1.0
