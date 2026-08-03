"""
Drives Patreon's post editor through the actual flow needed to swap
the link. Includes automatic Cloudflare challenge bypass using
playwright-captcha (async) – works headlessly in GitHub Actions.
"""

import os
import asyncio
from playwright.async_api import async_playwright
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

from config import PATREON_POST_URL

STORAGE_STATE_PATH = "patreon_session.json"


def _get_proxy_config():
    """Build proxy dict from environment variables if present."""
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")

    if host and port:
        proxy = {"server": f"http://{host}:{port}"}
        if username and password:
            proxy["username"] = username
            proxy["password"] = password
        return proxy
    return None


async def update_patreon_link(new_link: str) -> None:
    if not os.path.exists(STORAGE_STATE_PATH):
        raise FileNotFoundError(
            f"{STORAGE_STATE_PATH} not found. Run login_once.py locally first, "
            "then set the PATREON_SESSION_JSON secret in your repo."
        )

    proxy_config = _get_proxy_config()
    if proxy_config:
        print(f"Using proxy: {proxy_config['server']}", flush=True)

    async with async_playwright() as p:
        # Launch with stealth arguments
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--window-size=1920,1080",
            ]
        )
        context = await browser.new_context(
            storage_state=STORAGE_STATE_PATH,
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            proxy=proxy_config
        )
        page = await context.new_page()

        try:
            await _run_update_flow(page, new_link)
        except Exception:
            # Capture failure evidence
            try:
                await page.screenshot(path="patreon_failure.png", full_page=True)
            except Exception:
                pass
            try:
                with open("patreon_failure.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
            except Exception:
                pass
            raise
        finally:
            await browser.close()


async def _run_update_flow(page, new_link: str) -> None:
    await page.goto(PATREON_POST_URL, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    # Solve Cloudflare challenge if present
    title = await page.title()
    if "Just a moment..." in title or "Checking your browser" in title:
        print("Cloudflare challenge detected. Attempting to bypass...", flush=True)
        try:
            # Create solver and solve
            solver = ClickSolver(
                framework=FrameworkType.PLAYWRIGHT,
                page=page,
                max_attempts=5,
                attempt_delay=3
            )
            # Use async context manager to automatically clean up
            async with solver:
                await solver.solve_captcha(
                    captcha_container=page,
                    captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE
                )
            print("Cloudflare challenge solved successfully.", flush=True)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            # Save the challenge page for debugging
            with open("cloudflare_challenge.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise RuntimeError(f"Cloudflare bypass failed: {e}")

    # ---- Normal Patreon update flow ----
    await page.get_by_role("button", name="Edit").click(timeout=60000)
    await page.wait_for_timeout(1500)

    await page.get_by_role("button", name="Menu for additional actions").click(timeout=30000)
    await page.wait_for_timeout(500)

    await page.get_by_role("menuitem", name="Delete").click(timeout=30000)
    await page.wait_for_timeout(500)

    await page.get_by_role("button", name="Delete").click(timeout=30000)
    await page.wait_for_timeout(1000)

    await page.locator("button").filter(has_text="Link").click(timeout=30000)
    await page.wait_for_timeout(500)

    link_input = page.get_by_role("textbox", name="Type or paste URL")
    await link_input.click(timeout=30000)
    await link_input.fill(new_link)

    await page.wait_for_timeout(3000)

    await page.get_by_role("button", name="Update").click(timeout=30000)
    await page.wait_for_timeout(2000)
