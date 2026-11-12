"""Assurance sampling (paper §7, HLD §12).

Accepted outputs are taken in acceptance order and split into full blocks of W_a.
A partly filled last block is not sampled. Within each closed block, the sampling
unit is the cluster (batch, directive group). c clusters are drawn at random, and
j outputs are reviewed from each. Draws are seeded from run_id and block index, so
the same run always gives the same sample.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fenced_retry.validator.checks import GROUP_OF

NO_DIRECTIVE = "none"


def cluster_of(failing_sets: Sequence[Sequence[str]]) -> str:
    """Directive group of an accepted output: the group of its last failing attempt.

    The first failing check in sorted order picks the group. An output accepted on
    its first attempt has no directive and goes to the NO_DIRECTIVE cluster.
    """
    for failing in reversed(failing_sets):
        if failing:
            return GROUP_OF.get(sorted(failing)[0], "unknown")
    return NO_DIRECTIVE


def full_blocks(accepted: Sequence[str], w_a: int, include_tail: bool = False) -> list[list[str]]:
    """Consecutive blocks of w_a. With include_tail, the final partial block is kept too (D-060)."""
    if w_a < 1:
        raise ValueError("W_a must be >= 1")
    blocks = [list(accepted[b * w_a:(b + 1) * w_a]) for b in range(len(accepted) // w_a)]
    tail = list(accepted[(len(accepted) // w_a) * w_a:])
    if include_tail and tail:
        blocks.append(tail)
    return blocks


@dataclass(frozen=True)
class Sample:
    record_id: str
    block_index: int
    cluster: str


def sample_block(items: Sequence[tuple[str, str]], c: int, j: int, rng: random.Random,
                 n_min: int = 0) -> list[tuple[str, str]]:
    """items: (record_id, cluster) pairs of one block. Returns the chosen pairs.

    c clusters, j outputs from each. If that gives fewer than n_min outputs (few clusters,
    as when most outputs are first-attempt accepts in one cluster), the sample is topped up
    from the remaining outputs so the block still has n_min reviews (D-060).
    """
    by_cluster: dict[str, list[str]] = {}
    for rid, cl in items:
        by_cluster.setdefault(cl, []).append(rid)
    keys = sorted(by_cluster)
    chosen_keys = rng.sample(keys, min(c, len(keys)))
    picked: list[tuple[str, str]] = []
    for key in sorted(chosen_keys):
        members = sorted(by_cluster[key])
        for rid in sorted(rng.sample(members, min(j, len(members)))):
            picked.append((rid, key))
    if len(picked) < n_min:
        taken = {rid for rid, _ in picked}
        rest = [(rid, cl) for cl in keys for rid in sorted(by_cluster[cl]) if rid not in taken]
        extra = rng.sample(rest, min(n_min - len(picked), len(rest)))
        picked.extend(sorted(extra))
    return picked


def plan_samples(store: Any, run_id: str, *, w_a: int, c: int, j: int,
                 n_min: int = 0, include_tail: bool = True) -> list[Sample]:
    """Draw the assurance sample for a run and write it to the store (idempotent).

    include_tail (D-060): the final partial block of a batch is sampled as a block.
    Without it, a batch smaller than W_a is never sampled.
    """
    accepted = store.accepted_record_ids(run_id)
    clusters = {rid: store.cluster_for(run_id, rid) for rid in accepted}
    out: list[Sample] = []
    for b, block in enumerate(full_blocks(accepted, w_a, include_tail=include_tail)):
        rng = random.Random(f"{run_id}:{b}")
        for rid, cl in sample_block([(r, clusters[r]) for r in block], c, j, rng, n_min=n_min):
            s = Sample(rid, b, cl)
            store.add_sample(run_id, s.record_id, s.block_index, s.cluster)
            out.append(s)
    return out
