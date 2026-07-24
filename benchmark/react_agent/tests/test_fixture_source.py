from pathlib import Path

from samples import select
from sources import FixtureSource, parse_exposition

SCRAPE = """\
# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.5",model_name="openai/gpt-oss-20b"} 612.0
vllm:time_to_first_token_seconds_bucket{engine="0",le="+Inf",model_name="openai/gpt-oss-20b"} 3140.0
vllm:time_to_first_token_seconds_count{engine="0",model_name="openai/gpt-oss-20b"} 3140.0
vllm:time_to_first_token_seconds_sum{engine="0",model_name="openai/gpt-oss-20b"} 4476.2018349170685
vllm:num_requests_running{engine="0",model_name="openai/gpt-oss-20b"} 3.0
"""


def test_parses_metric_names_and_values():
    got = parse_exposition(SCRAPE, timestamp=1000)
    running = select(got, "vllm:num_requests_running")
    assert len(running) == 1
    assert running[0].value == 3.0
    assert running[0].timestamp == 1000


def test_parses_labels_including_le():
    got = parse_exposition(SCRAPE, timestamp=1000)
    buckets = select(got, "vllm:time_to_first_token_seconds_bucket", le="0.5")
    assert len(buckets) == 1
    assert buckets[0].value == 612.0
    assert buckets[0].labels["model_name"] == "openai/gpt-oss-20b"
    assert buckets[0].labels["engine"] == "0"


def test_preserves_the_inf_bucket_label():
    got = parse_exposition(SCRAPE, timestamp=1000)
    assert select(got, "vllm:time_to_first_token_seconds_bucket", le="+Inf")[0].value == 3140.0


def test_skips_comment_and_blank_lines():
    got = parse_exposition(SCRAPE, timestamp=1000)
    assert all(not s.metric.startswith("#") for s in got)
    assert len(got) == 5


def test_parses_scientific_notation():
    text = 'vllm:time_to_first_token_seconds_created{engine="0"} 1.7848325232461488e+09\n'
    got = parse_exposition(text, timestamp=1000)
    assert abs(got[0].value - 1784832523.2461488) < 1e-3


def test_parses_a_metric_with_no_labels():
    got = parse_exposition("process_open_fds 42.0\n", timestamp=1000)
    assert got[0].metric == "process_open_fds"
    assert got[0].labels == {}
    assert got[0].value == 42.0


def test_fixture_source_reads_scrapes_in_the_window(tmp_path: Path):
    for ts in (1000, 1001, 1002, 1003):
        (tmp_path / f"{ts}.prom").write_text(SCRAPE)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(start=1001, end=1002)
    stamps = sorted({s.timestamp for s in got})
    assert stamps == [1001, 1002]


def test_fixture_source_synthesizes_the_server_label(tmp_path: Path):
    (tmp_path / "1000.prom").write_text(SCRAPE)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(start=1000, end=1000)
    assert all(s.labels["server"] == "solab-x3" for s in got)


def test_fixture_source_returns_empty_for_a_window_with_no_scrapes(tmp_path: Path):
    (tmp_path / "1000.prom").write_text(SCRAPE)
    assert FixtureSource(tmp_path, server="solab-x3").fetch(start=2000, end=2100) == []
