from pathlib import Path

from fixtures.generate import SYSTEMS, synthesize, write_fixture
from metrics import find_anchor, mean_rate
from samples import select
from sources import FixtureSource


def test_every_system_is_generated():
    assert set(SYSTEMS) == {"mars", "lmcache", "mooncake", "recompute"}


def test_synthesize_is_deterministic_under_seed():
    a = synthesize("mars", seconds=120, seed=7)
    b = synthesize("mars", seconds=120, seed=7)
    assert a == b


def test_generated_scrapes_parse(tmp_path: Path):
    write_fixture("mars", tmp_path, start=1000, seconds=200, seed=1)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(1000, 1200)
    assert select(got, "vllm:time_to_first_token_seconds_count")


def test_contains_a_scrape_gap(tmp_path: Path):
    write_fixture("mars", tmp_path, start=1000, seconds=200, seed=1)
    present = {int(p.stem) for p in tmp_path.glob("*.prom")}
    missing = set(range(1000, 1200)) - present
    assert missing, "fixture must contain at least one missing scrape"


def test_anchor_skips_the_health_check_burst(tmp_path: Path):
    write_fixture("mars", tmp_path, start=1000, seconds=400, seed=1)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(1000, 1400)
    counts = select(got, "vllm:time_to_first_token_seconds_count")
    anchor = find_anchor(counts, sustain=10)
    assert anchor is not None
    # the probe burst sits near t=1020; the real load starts at t=1100
    assert anchor >= 1095, f"anchor landed on the probe burst at {anchor}"


def test_mars_has_the_lowest_mean_ttft(tmp_path: Path):
    means = {}
    for system in SYSTEMS:
        directory = tmp_path / system
        directory.mkdir()
        write_fixture(system, directory, start=1000, seconds=400, seed=1)
        got = FixtureSource(directory, server="solab-x3").fetch(1000, 1400)
        means[system] = mean_rate(
            select(got, "vllm:time_to_first_token_seconds_sum"),
            select(got, "vllm:time_to_first_token_seconds_count"),
            at=1350,
            window=30,
        )
    assert means["mars"] == min(means.values())
    assert means["recompute"] == max(means.values())


def test_recompute_has_no_external_cache_queries(tmp_path: Path):
    write_fixture("recompute", tmp_path, start=1000, seconds=400, seed=1)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(1000, 1400)
    queries = select(got, "vllm:external_prefix_cache_queries_total")
    assert all(sample.value == 0.0 for sample in queries)


def test_regenerating_removes_stale_scrapes(tmp_path: Path):
    # First write
    write_fixture("mars", tmp_path, start=1000, seconds=200, seed=1)
    first_run_files = sorted({p.stem for p in tmp_path.glob("*.prom")})

    # Write a stale file at the gap offset (GAP_AT=180 relative seconds)
    stale_file = tmp_path / "1180.prom"
    stale_file.write_text("stale content")
    files_with_stale = sorted({p.stem for p in tmp_path.glob("*.prom")})
    assert "1180" in files_with_stale

    # Regenerate with same parameters
    write_fixture("mars", tmp_path, start=1000, seconds=200, seed=1)
    second_run_files = sorted({p.stem for p in tmp_path.glob("*.prom")})

    # Stale file should be gone
    assert "1180" not in second_run_files
    # Contents should match the first run exactly
    assert second_run_files == first_run_files
