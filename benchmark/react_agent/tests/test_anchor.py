from metrics import find_anchor
from samples import Sample


def series(values, start=1000):
    """One sample per second."""
    return [
        Sample(timestamp=start + i, metric="vllm:ttft_count", labels={}, value=float(v))
        for i, v in enumerate(values)
    ]


def test_anchor_is_the_first_rising_timestamp():
    # flat for 5s, then rises for 20s
    values = [0] * 5 + list(range(1, 21))
    assert find_anchor(series(values), sustain=10) == 1004


def test_health_check_burst_before_the_real_load_is_rejected():
    # 3 probe requests at t=1010..1012, flat again, real load from t=1090
    values = [0] * 10 + [1, 2, 3] + [3] * 77 + list(range(4, 40))
    anchor = find_anchor(series(values), sustain=10)
    assert anchor == 1089, f"anchor landed on the probe burst at {anchor}"


def test_returns_none_when_the_counter_never_rises():
    assert find_anchor(series([0] * 60), sustain=10) is None


def test_returns_none_when_the_rise_never_sustains():
    # rises for 4s then stops, repeatedly -- never sustains 10s
    values = []
    for _ in range(6):
        values += [len(values), len(values) + 1, len(values) + 2, len(values) + 3]
        values += [values[-1]] * 8
    assert find_anchor(series(values), sustain=10) is None


def test_a_single_flat_second_inside_the_load_does_not_disqualify():
    # real load with one second of no completions -- still the anchor
    values = [0] * 5 + [1, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert find_anchor(series(values), sustain=10) == 1004


def test_sustain_window_is_measured_in_seconds_not_samples():
    # 5s scrape interval: 3 samples span 10s and must satisfy sustain=10
    coarse = [
        Sample(timestamp=1000 + i * 5, metric="c", labels={}, value=float(v))
        for i, v in enumerate([0, 0, 1, 5, 9, 14, 20])
    ]
    assert find_anchor(coarse, sustain=10) == 1005


def test_sparse_completions_still_anchor():
    # 1s scrape interval, but requests only complete every 3rd second (a
    # realistic cadence for multi-step agent turns at low concurrency).
    # Flat for 4s, then the counter ticks up every third second, sustained
    # well past the window. The old rule -- "at most one flat step in the
    # sustain window" -- counted flat *samples*, not elapsed time; a 10s
    # window here has seven flat steps (0,0,0,1,1,1,2,2,2,3,3) and the old
    # rule returned None even though the workload obviously sustained.
    values = [0] * 4
    counter = 0
    for i in range(30):
        if i % 3 == 0:
            counter += 1
        values.append(counter)
    assert find_anchor(series(values), sustain=10) == 1003
