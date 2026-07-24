from metrics import mean_rate, rate
from samples import Sample


def counter(values, start=100, step=1):
    return [
        Sample(timestamp=start + i * step, metric="c", labels={}, value=v)
        for i, v in enumerate(values)
    ]


def test_rate_of_steady_counter():
    # +2 per second across 10 seconds
    series = counter([float(2 * i) for i in range(11)])
    assert rate(series, at=110, window=10) == 2.0


def test_rate_returns_none_with_a_single_sample_in_window():
    series = counter([0.0, 5.0], start=100)
    assert rate(series, at=100, window=10) is None


def test_rate_ignores_samples_outside_the_window():
    series = counter([float(i) for i in range(21)], start=100)
    # window covers 105..110 -> 5 increments over 5 seconds
    assert rate(series, at=110, window=5) == 1.0


def test_rate_treats_a_decrease_as_a_counter_reset():
    # 0,10,20 then process restarts at 3,6
    series = counter([0.0, 10.0, 20.0, 3.0, 6.0], start=100)
    # increases: 10 + 10 + 3 (reset, counted whole) + 3 = 26 over 4 seconds
    assert rate(series, at=104, window=4) == 26.0 / 4.0


def test_rate_of_flat_counter_is_zero():
    series = counter([7.0] * 11)
    assert rate(series, at=110, window=10) == 0.0


def test_mean_rate_divides_sum_rate_by_count_rate():
    sums = counter([0.0, 10.0, 20.0])     # +10/s
    counts = counter([0.0, 4.0, 8.0])     # +4/s
    assert mean_rate(sums, counts, at=102, window=2) == 2.5


def test_mean_rate_is_none_when_no_requests_completed():
    sums = counter([5.0, 5.0, 5.0])
    counts = counter([9.0, 9.0, 9.0])
    assert mean_rate(sums, counts, at=102, window=2) is None


def test_mean_rate_matches_the_reference_snapshot():
    # 3140 requests totalling 4476.2018349170685s -> mean 1.426s
    sums = counter([0.0, 4476.2018349170685])
    counts = counter([0.0, 3140.0])
    got = mean_rate(sums, counts, at=101, window=1)
    assert abs(got - 1.4256) < 0.001
