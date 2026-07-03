"""Source parsers tested against saved real-shape fixtures (offline via respx)."""

from __future__ import annotations

import httpx
import respx

from helpers import load_fixture
from job_agent.sources import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource


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
