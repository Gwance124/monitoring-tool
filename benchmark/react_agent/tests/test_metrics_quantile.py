import math

from metrics import histogram_quantile
from samples import Sample

# Real boundaries and cumulative counts observed on openai/gpt-oss-20b,
# 2026-07-24. count=3140, sum=4476.2018349170685, mean=1.426s.
REFERENCE = [
    (0.001, 0), (0.005, 0), (0.01, 0), (0.02, 0), (0.04, 0),
    (0.06, 88), (0.08, 116), (0.1, 118), (0.25, 185), (0.5, 612),
    (0.75, 1005), (1.0, 1380), (2.5, 2851), (5.0, 3129), (7.5, 3132),
    (10.0, 3134), (20.0, 3134), (40.0, 3134), (80.0, 3139), (160.0, 3140),
    (640.0, 3140), (2560.0, 3140), (math.inf, 3140),
]


def buckets_from(pairs, start=100, span=10):
    """Two samples per bucket: zero at t=start, the cumulative count at t=start+span."""
    return {
        le: [
            Sample(timestamp=start, metric="b", labels={"le": str(le)}, value=0.0),
            Sample(timestamp=start + span, metric="b", labels={"le": str(le)}, value=float(c)),
        ]
        for le, c in pairs
    }


def test_p95_matches_the_reference_snapshot():
    got = histogram_quantile(0.95, buckets_from(REFERENCE), at=110, window=10)
    assert abs(got - 3.687) < 0.01


def test_p50_matches_the_reference_snapshot():
    got = histogram_quantile(0.50, buckets_from(REFERENCE), at=110, window=10)
    assert abs(got - 1.194) < 0.01


def test_interpolates_linearly_within_a_bucket():
    # 100 observations total; 50 at or below 1.0, all 100 at or below 3.0, so the
    # (1.0, 3.0] bucket holds 50 of them. p75 -> rank 75, which lands 75 - 50 = 25
    # observations into that 50-observation bucket: a fraction of 25/50 = 0.5,
    # i.e. halfway. Interpolated: 1.0 + 0.5 * (3.0 - 1.0) = 2.0.
    pairs = [(1.0, 50), (3.0, 100), (math.inf, 100)]
    got = histogram_quantile(0.75, buckets_from(pairs), at=110, window=10)
    assert abs(got - 2.0) < 1e-9


def test_first_bucket_interpolates_from_zero():
    pairs = [(2.0, 100), (math.inf, 100)]
    got = histogram_quantile(0.5, buckets_from(pairs), at=110, window=10)
    assert abs(got - 1.0) < 1e-9


def test_target_in_the_inf_bucket_returns_the_largest_finite_bound():
    pairs = [(1.0, 10), (math.inf, 100)]
    got = histogram_quantile(0.95, buckets_from(pairs), at=110, window=10)
    assert got == 1.0


def test_returns_none_when_no_observations_in_window():
    pairs = [(1.0, 0), (math.inf, 0)]
    assert histogram_quantile(0.95, buckets_from(pairs), at=110, window=10) is None


def test_returns_none_when_window_has_too_few_samples():
    buckets = {
        1.0: [Sample(timestamp=100, metric="b", labels={}, value=5.0)],
        math.inf: [Sample(timestamp=100, metric="b", labels={}, value=5.0)],
    }
    assert histogram_quantile(0.95, buckets, at=100, window=10) is None
