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
