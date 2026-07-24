import pytest

from samples import Sample, distinct_label, select


def _s(ts, metric, value, **labels):
    return Sample(timestamp=ts, metric=metric, labels=labels, value=value)


def test_select_filters_by_metric_name():
    samples = [
        _s(10, "vllm:ttft_count", 5.0),
        _s(10, "vllm:e2e_count", 7.0),
    ]
    got = select(samples, "vllm:ttft_count")
    assert [s.value for s in got] == [5.0]


def test_select_filters_by_label_equality():
    samples = [
        _s(10, "m", 1.0, server="solab-x3"),
        _s(10, "m", 2.0, server="solab-x9"),
    ]
    got = select(samples, "m", server="solab-x3")
    assert [s.value for s in got] == [1.0]


def test_select_returns_timestamp_sorted():
    samples = [_s(30, "m", 3.0), _s(10, "m", 1.0), _s(20, "m", 2.0)]
    assert [s.timestamp for s in select(samples, "m")] == [10, 20, 30]


def test_select_ignores_samples_missing_the_label():
    samples = [_s(10, "m", 1.0), _s(10, "m", 2.0, server="solab-x3")]
    assert [s.value for s in select(samples, "m", server="solab-x3")] == [2.0]


def test_distinct_label_collects_every_value():
    samples = [
        _s(10, "m", 1.0, server="solab-x3"),
        _s(11, "m", 2.0, server="solab-x9"),
        _s(12, "m", 3.0, server="solab-x3"),
    ]
    assert distinct_label(samples, "server") == {"solab-x3", "solab-x9"}


def test_sample_is_frozen():
    s = _s(10, "m", 1.0)
    with pytest.raises(Exception):
        s.value = 2.0
