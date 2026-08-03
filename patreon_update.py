"""
Drives Patreon's post editor through the actual flow needed to swap
the link. Includes automatic Cloudflare challenge bypass using
playwright-captcha – works headlessly in GitHub Actions.
"""

import os
import time
from playwright.sync_api import sync_playwright
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

from config import PATREON_POST_URL

STORAGE_STATE_PATH = "patreon_session.json"


def update_patreon_link(new_link: str) -> None:
    if not os.path.exists(STORAGE_STATE_PATH):
        raise FileNotFoundError(
            f"{STORAGE_STATE_PATH} not found. Run login_once.py locally first, "
            "then set the PATREON_SESSION_JSON secret in your repo."
        )

    with sync_playwright() as p:
        # Launch with stealth arguments to reduce detection
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--window-size=1920,1080",
            ]
        )
        context = browser.new_context(
            storage_state=STORAGE_STATE_PATH,
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            _run_update_flow(page, new_link)
        except Exception:
            # Capture failure evidence
            try:
                page.screenshot(path="patreon_failure.png", full_page=True)
            except Exception:
                pass
            try:
                with open("patreon_failure.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            raise
        finally:
            browser.close()


def _run_update_flow(page, new_link: str) -> None:
    # Navigate to the post first
    page.goto(PATREON_POST_URL, wait_until="domcontentloaded")
    time.sleep(2)

    # Detect and solve Cloudflare challenge if present
    if "Just a moment..." in page.title() or "Checking your browser" in page.title():
        print("Cloudflare challenge detected. Attempting to bypass...", flush=True)
        try:
            # Create solver AFTER navigation, with the page
            solver = ClickSolver(
                framework=FrameworkType.PLAYWRIGHT,
                page=page,
                max_attempts=5,
                attempt_delay=3
            )
            # Now solve the captcha
            solver.solve_captcha(
                captcha_container=page,
                captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE
            )
            print("Cloudflare challenge solved successfully.", flush=True)
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(3)
        except Exception as e:
            # Save the challenge page for debugging
            with open("cloudflare_challenge.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            raise RuntimeError(f"Cloudflare bypass failed: {e}")

    # ---- Normal Patreon update flow ----
    # 1. Open the post editor
    page.get_by_role("button", name="Edit").click(timeout=60000)
    page.wait_for_timeout(1500)

    # 2. Open the 3-dot menu on the existing link block
    page.get_by_role("button", name="Menu for additional actions").click(timeout=30000)
    page.wait_for_timeout(500)

    # 3. Click "Delete"
    page.get_by_role("menuitem", name="Delete").click(timeout=30000)
    page.wait_for_timeout(500)

    # 4. Confirm the deletion popup
    page.get_by_role("button", name="Delete").click(timeout=30000)
    page.wait_for_timeout(1000)

    # 5. Choose "Link" from the add-content picker
    page.locator("button").filter(has_text="Link").click(timeout=30000)
    page.wait_for_timeout(500)

    # 6. Type in the new link
    link_input = page.get_by_role("textbox", name="Type or paste URL")
    link_input.click(timeout=30000)
    link_input.fill(new_link)

    # 7. Give Patreon time to fetch a preview before saving
    page.wait_for_timeout(3000)

    # 8. Save
    page.get_by_role("button", name="Update").click(timeout=30000)
    page.wait_for_timeout(2000)
