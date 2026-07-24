"""Pure statistics over raw counter and histogram samples.

Every function here mirrors the corresponding PromQL operation so the fixture
path and the Prometheus path produce identical numbers.
"""

from __future__ import annotations

from samples import Sample


def rate(series: list[Sample], at: int, window: int) -> float | None:
    """Per-second increase of a monotonic counter over ``[at - window, at]``.

    A decrease between consecutive samples means the exporting process
    restarted; the post-reset value is added whole, matching PromQL ``rate``.
    Returns ``None`` when fewer than two samples fall inside the window.
    """
    inside = [s for s in series if at - window <= s.timestamp <= at]
    if len(inside) < 2:
        return None

    inside.sort(key=lambda s: s.timestamp)
    total = 0.0
    for previous, current in zip(inside, inside[1:]):
        if current.value >= previous.value:
            total += current.value - previous.value
        else:
            total += current.value

    # Divide by the observed span between samples, not the nominal window.
    # If a scrape gap means only part of the window has samples, dividing by
    # the nominal window would understate the rate by inventing quiet time
    # that was never measured. Dividing by what was actually observed keeps
    # gaps as gaps instead of fabricating values.
    span = inside[-1].timestamp - inside[0].timestamp
    if span <= 0:
        return None
    return total / span


def mean_rate(
    sum_series: list[Sample],
    count_series: list[Sample],
    at: int,
    window: int,
) -> float | None:
    """Exact mean observation over the window: ``rate(_sum) / rate(_count)``.

    Free of bucket quantization, unlike ``histogram_quantile``. Returns ``None``
    when no observations completed in the window, which is an undefined mean
    rather than a zero one.
    """
    sum_rate = rate(sum_series, at, window)
    count_rate = rate(count_series, at, window)
    if sum_rate is None or count_rate is None or count_rate == 0:
        return None
    return sum_rate / count_rate


def histogram_quantile(
    q: float,
    bucket_series: dict[float, list[Sample]],
    at: int,
    window: int,
) -> float | None:
    """Quantile estimate from cumulative histogram buckets, as PromQL computes it.

    ``bucket_series`` maps an ``le`` upper bound to that bucket's counter
    samples; use ``math.inf`` for the ``+Inf`` bucket. The estimate assumes
    observations are distributed uniformly inside the containing bucket, so its
    accuracy is bounded by that bucket's width.

    Returns ``None`` when nothing was observed in the window.
    """
    rates: list[tuple[float, float]] = []
    for upper_bound in sorted(bucket_series):
        bucket_rate = rate(bucket_series[upper_bound], at, window)
        if bucket_rate is None:
            return None
        rates.append((upper_bound, bucket_rate))

    if not rates:
        return None

    total = rates[-1][1]
    if total <= 0:
        return None

    finite_bounds = [bound for bound, _ in rates if bound != float("inf")]
    if not finite_bounds:
        return None

    target = q * total
    lower_bound = 0.0
    lower_count = 0.0
    for upper_bound, cumulative in rates:
        if cumulative >= target:
            if upper_bound == float("inf"):
                return max(finite_bounds)
            if cumulative <= lower_count:
                return upper_bound
            fraction = (target - lower_count) / (cumulative - lower_count)
            return lower_bound + (upper_bound - lower_bound) * fraction
        lower_bound = upper_bound
        lower_count = cumulative

    return max(finite_bounds)
