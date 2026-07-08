"""Source parsers tested against saved real-shape fixtures (offline via respx)."""

from __future__ import annotations

import httpx
import respx

from helpers import load_fixture
from job_agent.sources import (
    AshbySource,
    GreenhouseSource,
    LeverSource,
    RemoteOKSource,
    RemotiveSource,
    SmartRecruitersSearchSource,
    SmartRecruitersSource,
    build_source,
)


@respx.mock
def test_greenhouse_parses_real_shape():
    respx.route(method="GET", url__regex=r"https://boards-api\.greenhouse\.io/.*").mock(
        return_value=httpx.Response(200, json=load_fixture("greenhouse.json"))
    )
    jobs = GreenhouseSource("databricks").fetch()
    assert len(jobs) == 5
    job = jobs[0]
    assert job.source == "greenhouse"
    assert isinstance(job.id, str) and job.title
    assert job.url.startswith("http")
    assert job.posted_at is not None and job.posted_at.tzinfo is not None
    assert job.description  # HTML content was stripped to text


@respx.mock
def test_greenhouse_apply_url_is_the_hosted_form_not_the_careers_page():
    # absolute_url is the company careers page (a listing/search page with no
    # form — e.g. stripe.com/jobs/search?gh_jid=...), and /jobs/{id} redirects
    # back to it for boards with a careers-site override. The apply_url must be
    # the embed endpoint, which always serves the real application form.
    respx.route(method="GET", url__regex=r"https://boards-api\.greenhouse\.io/.*").mock(
        return_value=httpx.Response(200, json=load_fixture("greenhouse.json"))
    )
    job = GreenhouseSource("databricks").fetch()[0]
    assert job.apply_url == (
        f"https://boards.greenhouse.io/embed/job_app?for=databricks&token={job.id}"
    )
    assert job.url != job.apply_url          # careers page stays the human URL
    assert "databricks.com" in job.url       # ...and is untouched


@respx.mock
def test_lever_parses_country_and_epoch_date():
    respx.route(method="GET", url__regex=r"https://api\.lever\.co/.*").mock(
        return_value=httpx.Response(200, json=load_fixture("lever.json"))
    )
    jobs = LeverSource("palantir").fetch()
    assert jobs
    # Lever exposes a structured country and an epoch-ms createdAt.
    assert all(j.source == "lever" for j in jobs)
    assert any(j.country == "US" for j in jobs)
    assert all(j.posted_at is not None and j.posted_at.tzinfo is not None for j in jobs)


@respx.mock
def test_ashby_parses_published_and_country():
    respx.route(method="GET", url__regex=r"https://api\.ashbyhq\.com/.*").mock(
        return_value=httpx.Response(200, json=load_fixture("ashby.json"))
    )
    jobs = AshbySource("openai").fetch()
    assert jobs
    job = jobs[0]
    assert job.source == "ashby"
    assert job.posted_at is not None and job.posted_at.tzinfo is not None
    # Ashby gives a structured country string (e.g. "United States").
    assert any(j.country for j in jobs)


@respx.mock
def test_smartrecruiters_fetch_is_light_then_enrich_fills_description():
    # Detail route (…/postings/<digits>) must be registered before the list route.
    respx.route(method="GET", url__regex=r".*/postings/\d+.*").mock(
        return_value=httpx.Response(200, json=load_fixture("smartrecruiters_detail.json"))
    )
    respx.route(method="GET", url__regex=r".*/companies/SmartRecruiters/postings.*").mock(
        return_value=httpx.Response(200, json=load_fixture("smartrecruiters.json"))
    )
    src = SmartRecruitersSource("SmartRecruiters")
    jobs = src.fetch()
    assert jobs
    light = jobs[0]
    assert light.source == "smartrecruiters"
    assert light.description == ""             # list endpoint carries no description
    assert light.url.startswith("https://jobs.smartrecruiters.com/")

    enriched = src.enrich(light)
    assert enriched.description                 # detail endpoint filled it in
    assert enriched.id == light.id              # same job, new immutable copy
    assert light.description == ""              # original unchanged (immutability)


# --- cross-company sources (fixtures captured live, 2026-07-08) -----------------

@respx.mock
def test_sr_search_returns_jobs_across_companies():
    route = respx.route(
        method="GET", url__regex=r"https://jobs\.smartrecruiters\.com/sr-jobs/search.*"
    ).mock(return_value=httpx.Response(200, json=load_fixture("sr_search.json")))
    src = SmartRecruitersSearchSource("machine learning engineer")
    jobs = src.fetch()
    assert len(jobs) == 3
    assert {j.company for j in jobs} == {"Freshworks", "Bosch-HomeComfort"}  # cross-company
    assert "keyword=machine+learning+engineer" in str(route.calls[0].request.url)
    job = jobs[0]
    assert job.source == "sr-search"
    assert job.title == "Staff Engineer - Machine Learning"
    assert job.country == "us"                  # structured -> US filter works
    assert job.posted_at is not None and job.posted_at.tzinfo is not None
    assert job.url.startswith("https://jobs.smartrecruiters.com/Freshworks/")
    assert job.description == ""                # list carries no description


@respx.mock
def test_sr_search_enrich_uses_the_actions_details_url():
    # The detail posting id (actions.details) is NOT the search-result id, so
    # enrich must call the URL the search response itself provided.
    detail = respx.route(
        method="GET",
        url="https://api.smartrecruiters.com/v1/companies/Freshworks/postings/12674298410",
    ).mock(return_value=httpx.Response(200, json=load_fixture("smartrecruiters_detail.json")))
    respx.route(
        method="GET", url__regex=r"https://jobs\.smartrecruiters\.com/sr-jobs/search.*"
    ).mock(return_value=httpx.Response(200, json=load_fixture("sr_search.json")))
    src = SmartRecruitersSearchSource("machine learning engineer")
    job = src.fetch()[0]
    enriched = src.enrich(job)
    assert detail.called
    assert enriched.description
    assert job.description == ""                # immutability


@respx.mock
def test_remotive_parses_real_shape_as_remote_jobs():
    route = respx.route(
        method="GET", url__regex=r"https://remotive\.com/api/remote-jobs.*"
    ).mock(return_value=httpx.Response(200, json=load_fixture("remotive.json")))
    jobs = RemotiveSource("machine learning").fetch()
    assert len(jobs) == 3
    assert "search=machine+learning" in str(route.calls[0].request.url)
    ai = next(j for j in jobs if j.title == "Senior AI Engineer")
    assert ai.source == "remotive"
    assert ai.company == "Lemon.io"
    assert ai.remote is True                    # remote-only board
    assert ai.country is None                   # "Northern America, LATAM, …" -> unknown
    assert ai.posted_at is not None and ai.posted_at.tzinfo is not None
    assert ai.description and "<" not in ai.description   # HTML stripped


@respx.mock
def test_remoteok_skips_the_legal_element_and_maps_us_location():
    respx.route(method="GET", url__regex=r"https://remoteok\.com/api.*").mock(
        return_value=httpx.Response(200, json=load_fixture("remoteok.json"))
    )
    jobs = RemoteOKSource("machine-learning").fetch()
    assert len(jobs) == 3                       # the leading legal element is not a job
    assert all(j.source == "remoteok" and j.remote is True for j in jobs)
    us = next(j for j in jobs if j.location == "Remote - United States")
    assert us.country == "US"                   # loose US signal recognized
    cambridge = next(j for j in jobs if j.location == "Cambridge")
    assert cambridge.country is None            # ambiguous stays unknown
    assert all(j.posted_at is not None and j.posted_at.tzinfo is not None for j in jobs)


def test_guess_us_country_never_marks_foreign_locations_as_us():
    # The leak: ",\s*XX" matched ANY two capitals after a comma, so these got
    # country="US" stamped at the source — which BYPASSED the location stage's
    # own inference. guess_us_country must agree with geo.infer_country.
    from job_agent.sources.base import guess_us_country
    assert guess_us_country("London, UK") is None
    assert guess_us_country("Budapest, BU") is None
    assert guess_us_country("Madrid, MD") is None
    assert guess_us_country("Dublin") is None
    assert guess_us_country("Austin, TX") == "US"
    assert guess_us_country("Remote - United States") == "US"
    assert guess_us_country(None) is None


def test_factory_builds_the_cross_company_sources():
    assert isinstance(build_source("sr-search", "ml engineer"), SmartRecruitersSearchSource)
    assert isinstance(build_source("remotive", "ml"), RemotiveSource)
    assert isinstance(build_source("remoteok", "machine-learning"), RemoteOKSource)
