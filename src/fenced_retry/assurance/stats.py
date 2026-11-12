"""Exact binomial helpers for the breaker (paper §5.5, §9.4). Standard library only."""

from __future__ import annotations

import math


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X > k) for X ~ Binomial(n, p)."""
    if k >= n:
        return 0.0
    if k < 0:
        return 1.0
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k + 1, n + 1))


def phi_from_null(w_d: int, p0: float, alpha: float) -> tuple[int, float]:
    """Analytic dead-letter threshold (paper §9.4).

    Under a nominal dead-letter rate p0, a window of w_d records trips when the count
    exceeds k. Return the smallest k with P(count > k) <= alpha, and phi = k / w_d.
    Because the breaker trips on fraction > phi, a false pause has probability <= alpha.
    """
    if not 0.0 <= p0 <= 1.0 or not 0.0 < alpha < 1.0 or w_d < 1:
        raise ValueError("need 0 <= p0 <= 1, 0 < alpha < 1, w_d >= 1")
    for k in range(w_d + 1):
        if binom_sf(k, w_d, p0) <= alpha:
            return k, k / w_d
    return w_d, 1.0


def clopper_pearson_lower(k: int, n: int, gamma: float) -> float:
    """One-sided exact lower confidence bound at level gamma for a proportion k/n."""
    if n <= 0:
        raise ValueError("n must be positive")
    if k <= 0:
        return 0.0
    if k > n:
        raise ValueError("k > n")
    target = 1.0 - gamma
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        # P(X >= k | p) is increasing in p; find p where it equals the target.
        if binom_sf(k - 1, n, mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def required_clusters(alpha: float, eps_c: float) -> int:
    """c >= ln(alpha) / ln(1 - eps_c): clusters to sample so a wrong-cluster rate eps_c is seen (paper §7)."""
    if not (0.0 < alpha < 1.0 and 0.0 < eps_c < 1.0):
        raise ValueError("alpha and eps_c must be in (0, 1)")
    return math.ceil(math.log(alpha) / math.log(1.0 - eps_c))


def required_independent(alpha: float, eps: float) -> int:
    """n >= ln(alpha) / ln(1 - eps): reviews needed to see a rate eps under independence (paper §7)."""
    if not (0.0 < alpha < 1.0 and 0.0 < eps < 1.0):
        raise ValueError("alpha and eps must be in (0, 1)")
    return math.ceil(math.log(alpha) / math.log(1.0 - eps))
