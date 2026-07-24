"""Generate fixture scrapes shaped like the real vLLM endpoint.

Bucket boundaries and label sets come from a live capture taken 2026-07-24
against openai/gpt-oss-20b (count=3140, sum=4476.2s, mean=1.426s, p95~3.687s).
Values are synthetic; the structure is real.

The data deliberately contains the cases that break naive implementations: a
health-check burst before the real load, a warm-up ramp, sample noise, and a
missing scrape.
"""

from __future__ import annotations

import pathlib
import random

BUCKETS = (
    0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0,
    2.5, 5.0, 7.5, 10.0, 20.0, 40.0, 80.0, 160.0, 640.0, 2560.0,
)

MODEL = "openai/gpt-oss-20b"

# The two extra histograms below piggyback on the same bucket boundaries as
# TTFT/e2e (see the module docstring: boundaries come from one live capture).
FAMILIES = (
    "time_to_first_token_seconds",
    "e2e_request_latency_seconds",
    "request_queue_time_seconds",
    "request_prefill_time_seconds",
)

# mean TTFT (seconds), mean e2e (seconds), external cache hit ratio
SYSTEMS = {
    "mars":      (0.86, 2.71, 0.82),
    "lmcache":   (1.13, 3.29, 0.64),
    "mooncake":  (1.27, 3.55, 0.58),
    "recompute": (1.71, 4.42, 0.00),
}

# Queue delay is small and rises only slightly under load; scaling it off the
# TTFT base keeps its per-system ordering (mars best .. recompute worst)
# identical to TTFT's, so queue never makes MARS look worse than the others.
QUEUE_FRACTION = 0.02

# Prefill time is part of what TTFT measures, so generating it as a fraction
# of that same request's TTFT observation guarantees prefill can never exceed
# TTFT for the same request -- and therefore not at the aggregate p95 either.
PREFILL_FRACTION_RANGE = (0.30, 0.60)

PROBE_AT = 20        # seconds after start: health-check burst
LOAD_AT = 100        # seconds after start: real workload begins
WARMUP = 60          # seconds of elevated latency after LOAD_AT
GAP_AT = 180          # seconds after start: one missing scrape


def _ttft_for(second: int, base: float, rng: random.Random) -> float:
    """Per-request TTFT, elevated during warm-up, with noise."""
    elapsed = second - LOAD_AT
    warm = 1.0 + 0.65 * max(0.0, 1.0 - elapsed / WARMUP)
    return max(0.02, base * warm * rng.uniform(0.72, 1.34))


def _queue_for(second: int, base: float, rng: random.Random) -> float:
    """Per-request queue delay: small, near zero, rising slightly under load."""
    elapsed = second - LOAD_AT
    warm = 1.0 + 0.5 * max(0.0, 1.0 - elapsed / WARMUP)
    return max(0.001, base * warm * rng.uniform(0.4, 1.6))


def _accumulate(bucket: dict, observation: float) -> None:
    """Fold one observation into a histogram bucket accumulator in place."""
    bucket["count"] += 1
    bucket["sum"] += observation
    for bound in BUCKETS:
        if observation <= bound:
            bucket[bound] += 1


def _render(counters: dict, buckets: dict) -> str:
    lines = [
        "# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.",
        "# TYPE vllm:time_to_first_token_seconds histogram",
    ]
    labels = f'engine="0",model_name="{MODEL}"'
    for family in FAMILIES:
        for bound in BUCKETS:
            count = buckets[family][bound]
            lines.append(
                f'vllm:{family}_bucket{{engine="0",le="{bound}",model_name="{MODEL}"}} {count}.0'
            )
        total = buckets[family]["count"]
        lines.append(f'vllm:{family}_bucket{{engine="0",le="+Inf",model_name="{MODEL}"}} {total}.0')
        lines.append(f"vllm:{family}_count{{{labels}}} {total}.0")
        lines.append(f"vllm:{family}_sum{{{labels}}} {buckets[family]['sum']:.6f}")
    for name, value in counters.items():
        lines.append(f"vllm:{name}{{{labels}}} {value}")
    return "\n".join(lines) + "\n"


def synthesize(system: str, seconds: int, seed: int) -> dict[int, str]:
    """Relative second -> exposition text. Deterministic for a given seed."""
    base_ttft, base_e2e, hit_ratio = SYSTEMS[system]
    queue_base = base_ttft * QUEUE_FRACTION
    rng = random.Random(f"{system}:{seed}")

    families = {
        family: {"count": 0, "sum": 0.0, **{bound: 0 for bound in BUCKETS}}
        for family in FAMILIES
    }
    queries = hits = recomputed = successes = 0
    scrapes: dict[int, str] = {}

    for second in range(seconds):
        if second == PROBE_AT:
            arrivals = 3
        elif second < LOAD_AT:
            arrivals = 0
        else:
            arrivals = rng.randint(9, 15)

        for _ in range(arrivals):
            ttft_observation = _ttft_for(second, base_ttft, rng)
            e2e_observation = _ttft_for(second, base_e2e, rng)
            queue_observation = _queue_for(second, queue_base, rng)
            # Derived from this same request's TTFT observation so it is
            # structurally impossible for prefill to exceed TTFT.
            prefill_observation = ttft_observation * rng.uniform(*PREFILL_FRACTION_RANGE)

            _accumulate(families["time_to_first_token_seconds"], ttft_observation)
            _accumulate(families["e2e_request_latency_seconds"], e2e_observation)
            _accumulate(families["request_queue_time_seconds"], queue_observation)
            _accumulate(families["request_prefill_time_seconds"], prefill_observation)
            successes += 1
            recomputed += rng.randint(40, 120) if hit_ratio == 0 else rng.randint(4, 30)
            if hit_ratio > 0:
                queries += 1
                if rng.random() < hit_ratio:
                    hits += 1

        counters = {
            "external_prefix_cache_queries_total": queries,
            "external_prefix_cache_hits_total": hits,
            "prompt_tokens_recomputed_total": recomputed,
            "request_success_total": successes,
        }
        scrapes[second] = _render(counters, families)

    scrapes.pop(GAP_AT, None)
    return scrapes


def write_fixture(
    system: str,
    out_dir: pathlib.Path,
    start: int,
    seconds: int,
    seed: int,
) -> None:
    """Write ``<unix_timestamp>.prom`` scrapes for one system."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Delete any stale .prom files from previous runs. A stale file from an
    # earlier run (e.g. with different --seed or --seconds) would be
    # indistinguishable from a generated one and could silently fill the
    # deliberate scrape gap, breaking the determinism guarantee.
    for stale in out_dir.glob("*.prom"):
        stale.unlink()
    for offset, text in synthesize(system, seconds, seed).items():
        (out_dir / f"{start + offset}.prom").write_text(text, encoding="utf-8")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path(__file__).parent)
    parser.add_argument("--start", type=int, default=1784912400)
    parser.add_argument("--seconds", type=int, default=520)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    for system in SYSTEMS:
        write_fixture(system, args.out / system, args.start, args.seconds, args.seed)
        print(args.out / system)


if __name__ == "__main__":
    main()
