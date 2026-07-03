"""Playwright launch helper.

Real runs open a VISIBLE (non-headless) Chromium so the human can log in, solve
captchas, and watch what's filled. Demo runs may go headless. Playwright is
imported lazily inside the context manager, so importing the ``apply`` package
(and running its pure-logic tests) never requires Playwright to be installed.

One-time setup for live runs:

    pip install -e .
    playwright install chromium
"""

from __future__ import annotations

from contextlib import contextmanager


class PlaywrightNotInstalled(RuntimeError):
    """Raised with setup guidance when Playwright/Chromium isn't available."""


@contextmanager
def open_browser(*, headless: bool = False, slow_mo_ms: int = 0):
    """Yield a ready Playwright ``page``, tearing the browser down after.

    ``headless=False`` (the default for real runs) shows the window so the human
    can act in it during a pause.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise PlaywrightNotInstalled(
            "Playwright is required for assisted apply. Install it with:\n"
            "    pip install -e .\n"
            "    playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise PlaywrightNotInstalled(
                "Chromium isn't installed for Playwright. Run:\n"
                "    playwright install chromium"
            ) from exc
        context = browser.new_context(accept_downloads=False)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
