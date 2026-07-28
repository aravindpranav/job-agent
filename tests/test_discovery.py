"""Board-token discovery: variant derivation, probe semantics, caching, output.

Probe responses were verified live (2026-07-21):
  greenhouse valid -> 200 {"jobs": [...]}; invalid -> 404 JSON
  ashby      valid -> 200 {"jobs": [...]}; invalid -> 404 plain-text
  lever      valid -> 200 [ ...postings ]; invalid -> 404 JSON
"""

from __future__ import annotations

from pathlib import Path

from job_agent.discovery import (
    ATS_ORDER,
    DiscoveryCache,
    discover_boards,
    format_yaml_block,
    looks_valid,
    probe_url,
    token_variants,
)


# --- token derivation --------------------------------------------------------

def test_single_word_name_yields_one_token():
    assert token_variants("OpenAI") == ["openai"]


def test_multi_word_name_yields_joined_and_hyphenated():
    assert token_variants("Modern Treasury") == ["moderntreasury", "modern-treasury"]


def test_punctuation_and_dots_are_folded():
    assert token_variants("H2O.ai") == ["h2oai", "h2o-ai"]


def test_ampersand_becomes_and():
    assert token_variants("Weights & Biases") == [
        "weightsandbiases", "weights-and-biases"]


def test_corporate_suffixes_are_stripped():
    assert token_variants("Datadog, Inc.") == ["datadog"]
    assert token_variants("Acme LLC") == ["acme"]


def test_variants_are_deduped_preserving_order():
    assert token_variants("Stripe Stripe") == ["stripestripe", "stripe-stripe"]
    assert token_variants("stripe") == ["stripe"]


# --- probe semantics ---------------------------------------------------------

def test_probe_urls_match_the_verified_endpoints():
    assert probe_url("greenhouse", "acme") == \
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    assert probe_url("ashby", "acme") == \
        "https://api.ashbyhq.com/posting-api/job-board/acme"
    assert probe_url("lever", "acme") == \
        "https://api.lever.co/v0/postings/acme?mode=json"


def test_valid_shapes_per_ats():
    assert looks_valid("greenhouse", 200, {"jobs": []}) is True
    assert looks_valid("ashby", 200, {"jobs": [{"id": "x"}]}) is True
    assert looks_valid("lever", 200, [{"id": "x"}]) is True


def test_wrong_shape_on_200_is_not_valid():
    assert looks_valid("greenhouse", 200, [1, 2]) is False
    assert looks_valid("lever", 200, {"jobs": []}) is False
    assert looks_valid("ashby", 200, None) is False


def test_definitive_404_is_invalid_but_transient_is_unknown():
    assert looks_valid("greenhouse", 404, {"status": 404}) is False
    assert looks_valid("ashby", 404, None) is False
    assert looks_valid("lever", 401, None) is False
    assert looks_valid("greenhouse", 429, None) is None   # rate-limited: unknown
    assert looks_valid("greenhouse", 500, None) is None   # server hiccup: unknown
    assert looks_valid("greenhouse", None, None) is None  # network error: unknown


# --- discovery flow ----------------------------------------------------------

def _fetcher(responses):
    """Fake fetcher: url -> (status, body); records calls."""
    calls = []

    def fetch(url):
        calls.append(url)
        return responses.get(url, (404, None))

    fetch.calls = calls
    return fetch


def test_first_validated_ats_wins_and_stops_probing_that_company(tmp_path):
    gh = probe_url("greenhouse", "acme")
    fetch = _fetcher({gh: (200, {"jobs": []})})
    result = discover_boards(["Acme"], cache=DiscoveryCache(tmp_path / "c.json"),
                             fetcher=fetch, sleep=lambda s: None)
    assert [(h.ats, h.token) for h in result.hits] == [("greenhouse", "acme")]
    assert fetch.calls == [gh]        # no further variants or ATSs probed


def test_ats_probe_order_is_greenhouse_lever_ashby():
    assert ATS_ORDER == ("greenhouse", "lever", "ashby")


def test_negative_results_are_cached_so_reruns_make_no_requests(tmp_path):
    cache_path = tmp_path / "c.json"
    fetch = _fetcher({})              # everything 404s
    r1 = discover_boards(["Nowhere Co"], cache=DiscoveryCache(cache_path),
                         fetcher=fetch, sleep=lambda s: None)
    assert r1.hits == () and fetch.calls   # probed and missed

    fetch2 = _fetcher({})
    r2 = discover_boards(["Nowhere Co"], cache=DiscoveryCache(cache_path),
                         fetcher=fetch2, sleep=lambda s: None)
    assert r2.hits == ()
    assert fetch2.calls == []         # all negatives served from cache


def test_transient_errors_are_reported_and_never_cached(tmp_path):
    cache_path = tmp_path / "c.json"
    url = probe_url("greenhouse", "flaky")
    fetch = _fetcher({url: (500, None)})
    r1 = discover_boards(["flaky"], cache=DiscoveryCache(cache_path),
                         fetcher=fetch, sleep=lambda s: None)
    assert r1.transient                # surfaced, not silently swallowed

    # a later run re-probes (the 500 was not cached) and can now validate
    fetch2 = _fetcher({url: (200, {"jobs": []})})
    r2 = discover_boards(["flaky"], cache=DiscoveryCache(cache_path),
                         fetcher=fetch2, sleep=lambda s: None)
    assert [(h.ats, h.token) for h in r2.hits] == [("greenhouse", "flaky")]


def test_positive_results_are_cached_too(tmp_path):
    cache_path = tmp_path / "c.json"
    url = probe_url("greenhouse", "acme")
    discover_boards(["acme"], cache=DiscoveryCache(cache_path),
                    fetcher=_fetcher({url: (200, {"jobs": []})}), sleep=lambda s: None)
    fetch2 = _fetcher({})
    r2 = discover_boards(["acme"], cache=DiscoveryCache(cache_path),
                         fetcher=fetch2, sleep=lambda s: None)
    assert [(h.ats, h.token) for h in r2.hits] == [("greenhouse", "acme")]
    assert fetch2.calls == []


def test_rate_limit_sleeps_between_network_probes(tmp_path):
    sleeps = []
    fetch = _fetcher({})
    discover_boards(["One Two"], cache=DiscoveryCache(tmp_path / "c.json"),
                    fetcher=fetch, sleep=sleeps.append)
    # one sleep per network request, ~2 req/s
    assert len(sleeps) == len(fetch.calls)
    assert all(s == 0.5 for s in sleeps)


def test_comments_and_blank_lines_are_ignored(tmp_path):
    fetch = _fetcher({probe_url("greenhouse", "acme"): (200, {"jobs": []})})
    result = discover_boards(["# comment", "", "acme"],
                             cache=DiscoveryCache(tmp_path / "c.json"),
                             fetcher=fetch, sleep=lambda s: None)
    assert [(h.ats, h.token) for h in result.hits] == [("greenhouse", "acme")]


# --- output format -----------------------------------------------------------

def test_yaml_block_matches_search_profile_source_format(tmp_path):
    fetch = _fetcher({
        probe_url("greenhouse", "acme"): (200, {"jobs": []}),
        probe_url("greenhouse", "beacon"): (404, None),
        probe_url("lever", "beacon"): (200, [{"id": "j"}]),
    })
    result = discover_boards(["acme", "beacon"],
                             cache=DiscoveryCache(tmp_path / "c.json"),
                             fetcher=fetch, sleep=lambda s: None)
    block = format_yaml_block(result.hits)
    assert "- { ats: greenhouse,      board: acme }" in block
    assert "- { ats: lever,           board: beacon }" in block
