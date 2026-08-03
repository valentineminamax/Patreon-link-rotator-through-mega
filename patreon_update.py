"""
Drives Patreon's post editor through the actual flow needed to swap
the link (this UI doesn't support just retyping text over the old
link — the old link block has to be deleted and a new one added):

  1. Open the post editor
  2. Open the 3-dot menu on the existing link block
  3. Click "Delete"
  4. Confirm the deletion in the popup that appears
  5. Pick "Link" from the add-content option picker
  6. Type the new URL into the link field
  7. Wait for Patreon to fetch a link preview
  8. Click the button that saves the change

--------------------------------------------------------------------
UPDATE (this round): the "Edit" button timeout was NOT a selector
problem, despite looking like one. patreon_failure.html from the last
real failure had <title>Just a moment...</title> and contained
Cloudflare challenge markers (__cf_chl, challenge-platform). The
screenshot confirmed it: Playwright landed on a Cloudflare "Verify you
are human" interstitial, not the real Patreon post. So get_by_role
was correctly waiting for a button that could never appear on that
page. Root cause is Cloudflare's bot-detection flagging the
combination of (a) a GitHub Actions runner's datacenter IP and (b)
headless Chromium's automation fingerprint (navigator.webdriver, etc).

This version:
  - Launches with a less-fingerprintable context (real-looking UA,
    viewport, locale, timezone, and the AutomationControlled flag
    disabled) -- a config change, not a bypass tool. This MAY let a
    non-interactive challenge clear on its own.
  - Explicitly detects the Cloudflare interstitial by page title after
    goto, and retries with a short backoff a few times before giving
    up, instead of silently burning the whole 30s locator timeout on
    a button that was never going to appear.
  - If it's still blocked after retries, raises a clear, distinct
    error ("blocked by Cloudflare challenge") instead of a generic
    Playwright timeout on get_by_role("Edit") -- so the next failure
    is unambiguous from the traceback alone, no screenshot-reading
    required to tell "selector broke" apart from "never reached the
    page".

If this still gets blocked every run, it likely means the datacenter
IP itself (not just the headless fingerprint) is the trigger, and no
amount of Playwright tuning inside GitHub Actions will fix it -- see
HANDOFF.md for the self-hosted-runner fallback.
--------------------------------------------------------------------
"""

import os

from playwright.sync_api import sync_playwright

from config import PATREON_POST_URL

STORAGE_STATE_PATH = "patreon_session.json"

# A real, current desktop Chrome UA string. Keep this roughly in sync
# with whatever Chromium version `playwright install chromium` pulls
# down -- a wildly mismatched UA-vs-engine combo is itself a bot signal.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

CLOUDFLARE_CHALLENGE_TITLE_MARKERS = ("just a moment", "attention required")

# How many times to retry loading the page if we land on a Cloudflare
# interstitial, and how long to wait between tries. Non-interactive
# challenges typically clear in a few seconds if they're going to
# clear at all -- more retries than this is unlikely to help and just
# burns CI minutes.
MAX_CHALLENGE_RETRIES = 3
CHALLENGE_RETRY_WAIT_MS = 8000


class CloudflareBlockedError(RuntimeError):
    """Raised when Patreon's Cloudflare front-end never lets us
    through to the real page, as distinct from a real selector /
    Patreon-UI problem."""


def update_patreon_link(new_link: str) -> None:
    if not os.path.exists(STORAGE_STATE_PATH):
        raise FileNotFoundError(
            f"{STORAGE_STATE_PATH} not found. Run login_once.py locally first, "
            "then set the PATREON_SESSION_JSON secret in your repo."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            storage_state=STORAGE_STATE_PATH,
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = context.new_page()

        try:
            _goto_past_cloudflare(page, PATREON_POST_URL)
            _run_update_flow(page, new_link)
        except Exception:
            # Capture direct visual evidence of what the page actually
            # looked like at the moment of failure -- e.g. a login page
            # (session didn't restore) vs. a Cloudflare interstitial
            # (bot detection) vs. the real post in a broken state
            # (selector issue) vs. something else entirely. Saved here
            # so rotate.yml can upload them as a downloadable artifact
            # on failure, instead of us having to guess from a
            # traceback alone.
            try:
                page.screenshot(path="patreon_failure.png", full_page=True)
            except Exception as screenshot_err:
                print(f"Could not capture failure screenshot: {screenshot_err}", flush=True)
            try:
                with open("patreon_failure.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception as html_err:
                print(f"Could not capture failure HTML: {html_err}", flush=True)
            raise
        finally:
            browser.close()


def _looks_like_cloudflare_challenge(page) -> bool:
    try:
        title = (page.title() or "").lower()
    except Exception:
        return False
    return any(marker in title for marker in CLOUDFLARE_CHALLENGE_TITLE_MARKERS)


def _goto_past_cloudflare(page, url: str) -> None:
    """Navigate to url, retrying if Cloudflare's interstitial shows up
    instead of the real page. Raises CloudflareBlockedError (not a
    generic timeout) if it never clears, so the traceback tells you
    immediately that this is a bot-detection block, not a selector
    problem."""
    for attempt in range(1, MAX_CHALLENGE_RETRIES + 1):
        page.goto(url, wait_until="networkidle")

        if not _looks_like_cloudflare_challenge(page):
            print(f"Reached real page on attempt {attempt}/{MAX_CHALLENGE_RETRIES}.", flush=True)
            return

        print(
            f"Attempt {attempt}/{MAX_CHALLENGE_RETRIES}: landed on a Cloudflare "
            f"challenge page (title: {page.title()!r}). Waiting "
            f"{CHALLENGE_RETRY_WAIT_MS}ms before retrying...",
            flush=True,
        )
        page.wait_for_timeout(CHALLENGE_RETRY_WAIT_MS)

    raise CloudflareBlockedError(
        f"Still on a Cloudflare challenge page after {MAX_CHALLENGE_RETRIES} attempts "
        f"(final title: {page.title()!r}). This is Cloudflare bot-detection blocking "
        "the request, not a broken selector -- the real Patreon post was never "
        "reached. This usually means the runner's IP (not just the headless "
        "browser fingerprint) is getting flagged. See HANDOFF.md for the "
        "self-hosted-runner fallback if this happens on every run."
    )


def _run_update_flow(page, new_link: str) -> None:
        # 1. Open the post editor
        page.get_by_role("button", name="Edit").click()
        page.wait_for_timeout(1500)

        # 2. Open the 3-dot menu on the existing link block
        page.get_by_role("button", name="Menu for additional actions").click()
        page.wait_for_timeout(500)

        # 3. Click "Delete"
        page.get_by_role("menuitem", name="Delete").click()
        page.wait_for_timeout(500)

        # 4. Confirm the deletion popup
        page.get_by_role("button", name="Delete").click()
        page.wait_for_timeout(1000)

        # 5. Choose "Link" from the add-content picker
        page.locator("button").filter(has_text="Link").click()
        page.wait_for_timeout(500)

        # 6. Type in the new link
        link_input = page.get_by_role("textbox", name="Type or paste URL")
        link_input.click()
        link_input.fill(new_link)

        # 7. Give Patreon time to fetch a preview before saving
        page.wait_for_timeout(3000)

        # 8. Save
        page.get_by_role("button", name="Update").click()
        page.wait_for_timeout(2000)
