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


def find_anchor(count_series: list[Sample], sustain: int = 10) -> int | None:
    """Timestamp of the first served token of a sustained workload.

    Anchoring on observed request activity rather than wall-clock start makes
    per-system startup delay cancel out: ``elapsed = 0`` denotes the same
    physical event in every run.

    A candidate qualifies when the counter rises at that timestamp and never
    stalls for long inside the sustain window: the longest contiguous span of
    time over which the counter did not increase must not exceed
    ``sustain / 2``. What separates a workload that kept going from one that
    rose and stopped is not where the progress happened but how long progress
    paused.

    The span is measured in seconds between samples, not in samples, so the
    rule is independent of the scrape interval -- at a fast interval a normal
    gap between completions spans many flat scrapes, and counting those
    scrapes would mistake ordinary cadence for a stall. The threshold derives
    from ``sustain`` itself rather than from sample spacing or a fitted
    constant, so it means the same thing at every configuration.

    ``sustain`` is a duration for the same reason; it rejects readiness probes
    and warm-up bursts, which rise briefly and stop, and it cannot be fooled
    by a later burst, because the quiet gap before that burst is itself a long
    flat span.

    The honest limitation: if the workload stalls for more than half the
    sustain window -- very low request rates, or long gaps between agent turns
    -- the anchor cannot be confirmed and the operator should raise
    ``--anchor-sustain``.
    """
    ordered = sorted(count_series, key=lambda s: s.timestamp)
    if len(ordered) < 2:
        return None

    for index in range(1, len(ordered)):
        previous, current = ordered[index - 1], ordered[index]
        if current.value <= previous.value:
            continue

        # The rise begins between ``previous`` and ``current``, so the last
        # instant the counter was still flat -- ``previous.timestamp`` -- is
        # the event being anchored to.
        anchor_ts = previous.timestamp
        deadline = anchor_ts + sustain
        window = [s for s in ordered if anchor_ts <= s.timestamp <= deadline]
        if not window or window[-1].timestamp < deadline:
            # Not enough data yet to confirm sustain; a closer rise cannot
            # be confirmed either, so guessing would misalign the run.
            return None

        # Longest contiguous stretch of seconds with no increase. A step that
        # does not increase extends the current stall by the time it spans; a
        # step that does increase ends the stall.
        longest_stall = 0
        current_stall = 0
        for prev_s, next_s in zip(window, window[1:]):
            if next_s.value > prev_s.value:
                current_stall = 0
            else:
                current_stall += next_s.timestamp - prev_s.timestamp
                longest_stall = max(longest_stall, current_stall)

        if longest_stall <= sustain / 2:
            return anchor_ts

    return None
