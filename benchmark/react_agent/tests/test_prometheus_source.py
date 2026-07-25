import math

import pytest

import sources
from sources import PrometheusError, PrometheusSource


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def install_fake_get(monkeypatch, payload_for_query):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["query"])
        return FakeResponse(payload_for_query(params["query"]))

    monkeypatch.setattr(sources.requests, "get", fake_get)
    return calls


def matrix(metric_labels, values):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": metric_labels, "values": values}],
        },
    }


def test_builds_selector_pinned_to_the_target(monkeypatch):
    calls = install_fake_get(monkeypatch, lambda q: matrix({}, []))
    PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1010)
    assert all('server="solab-x3"' in query for query in calls)


def test_includes_the_model_filter_when_given(monkeypatch):
    calls = install_fake_get(monkeypatch, lambda q: matrix({}, []))
    PrometheusSource("http://p7:9090", target="solab-x3", model="openai/gpt-oss-20b").fetch(1000, 1010)
    assert all('model_name="openai/gpt-oss-20b"' in query for query in calls)


def test_never_uses_promql_functions(monkeypatch):
    calls = install_fake_get(monkeypatch, lambda q: matrix({}, []))
    PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1010)
    for query in calls:
        assert "rate(" not in query
        assert "histogram_quantile" not in query


def test_converts_matrix_results_to_samples(monkeypatch):
    payload = matrix(
        {"__name__": "vllm:time_to_first_token_seconds_count", "server": "solab-x3", "le": "0.5"},
        [[1000, "5"], [1001, "9"]],
    )
    install_fake_get(monkeypatch, lambda q: payload)
    got = PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)
    counts = [s for s in got if s.metric == "vllm:time_to_first_token_seconds_count"]
    assert (counts[0].timestamp, counts[0].value) == (1000, 5.0)
    assert counts[0].labels["le"] == "0.5"
    assert "__name__" not in counts[0].labels


def test_aborts_when_the_window_spans_multiple_instances(monkeypatch):
    # NOTE: this payload is synthetic -- the selector already pins
    # `server="solab-x3"`, so real Prometheus cannot return a second `server`
    # value here; every returned series would carry exactly the pinned value.
    # The assertion is kept as a defense-in-depth check, but it is the
    # engine/model tests below that exercise a window Prometheus could
    # actually return.
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"__name__": "m", "server": "solab-x3"}, "values": [[1000, "1"]]},
                {"metric": {"__name__": "m", "server": "solab-x9"}, "values": [[1000, "2"]]},
            ],
        },
    }
    install_fake_get(monkeypatch, lambda q: payload)
    with pytest.raises(PrometheusError, match="solab-x9"):
        PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)


def test_aborts_when_a_metric_resolves_to_multiple_engines(monkeypatch):
    # Reachable in production: the selector pins `server`, but not `engine`.
    # A host running two vLLM engine processes for the same server label
    # matches this query twice, and the two series would silently merge
    # under one metric name.
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "vllm:time_to_first_token_seconds_sum",
                        "server": "solab-x3",
                        "engine": "0",
                    },
                    "values": [[1000, "1"]],
                },
                {
                    "metric": {
                        "__name__": "vllm:time_to_first_token_seconds_sum",
                        "server": "solab-x3",
                        "engine": "1",
                    },
                    "values": [[1000, "2"]],
                },
            ],
        },
    }
    install_fake_get(monkeypatch, lambda q: payload)
    with pytest.raises(PrometheusError, match="resolves to more than one time series"):
        PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)


def test_aborts_when_a_metric_resolves_to_multiple_models(monkeypatch):
    # Reachable in production: the selector pins `server`, but not
    # `model_name` unless --model is passed. A host serving two models
    # matches this query twice for the same server.
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "vllm:time_to_first_token_seconds_sum",
                        "server": "solab-x3",
                        "model_name": "openai/gpt-oss-20b",
                    },
                    "values": [[1000, "1"]],
                },
                {
                    "metric": {
                        "__name__": "vllm:time_to_first_token_seconds_sum",
                        "server": "solab-x3",
                        "model_name": "meta-llama/Llama-3-8B",
                    },
                    "values": [[1000, "2"]],
                },
            ],
        },
    }
    install_fake_get(monkeypatch, lambda q: payload)
    with pytest.raises(PrometheusError, match="resolves to more than one time series"):
        PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)


def test_histogram_buckets_do_not_trigger_the_abort(monkeypatch):
    # A single histogram series legitimately fans out into many `le` values.
    # The new per-metric/per-label-set check must exclude `le` or it would
    # abort on every histogram it ever sees.
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "vllm:time_to_first_token_seconds_bucket",
                        "server": "solab-x3",
                        "le": le,
                    },
                    "values": [[1000, "1"]],
                }
                for le in ("0.1", "0.5", "1.0", "2.5", "5.0", "+Inf")
            ],
        },
    }
    install_fake_get(monkeypatch, lambda q: payload)
    got = PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)
    buckets = [s for s in got if s.metric == "vllm:time_to_first_token_seconds_bucket"]
    assert {s.labels["le"] for s in buckets} == {"0.1", "0.5", "1.0", "2.5", "5.0", "+Inf"}


def test_raises_on_prometheus_error_status(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"status": "error", "error": "parse error"})

    monkeypatch.setattr(sources.requests, "get", fake_get)
    with pytest.raises(PrometheusError, match="parse error"):
        PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)


@pytest.mark.parametrize("target", ["", None])
def test_rejects_an_empty_target(target):
    with pytest.raises(ValueError, match="--target"):
        PrometheusSource("http://p7:9090", target=target)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": 'solab"x3'},
        {"target": "solab{x3"},
        {"target": "solab}x3"},
        {"target": "solab-x3", "model": 'openai/gpt"oss'},
    ],
)
def test_rejects_a_target_that_would_break_the_selector(kwargs):
    with pytest.raises(ValueError):
        PrometheusSource("http://p7:9090", **kwargs)


def test_drops_non_finite_values_from_query_range(monkeypatch):
    payload = matrix(
        {"__name__": "vllm:time_to_first_token_seconds_count", "server": "solab-x3"},
        [[1000, "NaN"], [1001, "9"]],
    )
    install_fake_get(monkeypatch, lambda q: payload)
    got = PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)
    counts = [s for s in got if s.metric == "vllm:time_to_first_token_seconds_count"]
    assert counts
    assert all(math.isfinite(s.value) for s in got)
    assert [s.value for s in counts] == [9.0]
