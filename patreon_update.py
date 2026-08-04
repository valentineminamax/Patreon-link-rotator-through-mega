"""
Drives Patreon's post editor with Cloudflare bypass using Patchright + proxy.
Proxy is only used for the browser, not for system commands.
"""

import os
import asyncio
from patchright.async_api import async_playwright
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

from config import PATREON_POST_URL

STORAGE_STATE_PATH = "patreon_session.json"


def _get_proxy_config():
    """Build proxy dict from environment variables."""
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")

    if host and port:
        proxy = {"server": f"http://{host}:{port}"}
        if username and password:
            proxy["username"] = username
            proxy["password"] = password
        print(f"✅ Using proxy: {proxy['server']} (data counted only for browser traffic)", flush=True)
        return proxy
    print("⚠️ No proxy configured – will use direct connection.", flush=True)
    return None


async def update_patreon_link(new_link: str) -> None:
    if not os.path.exists(STORAGE_STATE_PATH):
        raise FileNotFoundError(
            f"{STORAGE_STATE_PATH} not found. Run login_once.py locally first, "
            "then set the PATREON_SESSION_JSON secret in your repo."
        )

    proxy_config = _get_proxy_config()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--window-size=1920,1080",
                "--disable-features=IsolateOrigins,site-per-process",
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
            proxy=proxy_config,
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await context.new_page()

        # Set a default timeout for all actions (60 seconds)
        page.set_default_timeout(60000)

        try:
            await _run_update_flow(page, new_link)
        except Exception:
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
    # Navigate to the post with a longer timeout (90 seconds)
    await page.goto(PATREON_POST_URL, wait_until="domcontentloaded", timeout=90000)
    await asyncio.sleep(3)

    title = await page.title()
    print(f"Page title: {title}", flush=True)

    # Check for Cloudflare challenge – only if it appears
    if "Just a moment..." in title or "Checking your browser" in title:
        print("Cloudflare challenge detected. Attempting to bypass...", flush=True)

        solver = ClickSolver(
            framework=FrameworkType.PATCHRIGHT,
            page=page,
            max_attempts=5,
            attempt_delay=5,
        )

        try:
            async with solver:
                try:
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE
                    )
                except Exception:
                    print("Turnstile solver failed, trying Interstitial...", flush=True)
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                    )
            print("✅ Cloudflare challenge solved successfully.", flush=True)
            await page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(3)
        except Exception as e:
            with open("cloudflare_challenge.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise RuntimeError(f"Cloudflare bypass failed: {e}")

    # ---- Normal Patreon update flow ----
    await page.get_by_role("button", name="Edit").click(timeout=60000)
    await page.wait_for_timeout(1500)

    await page.get_by_role("button", name="Menu for additional actions").click(timeout=60000)
    await page.wait_for_timeout(500)

    await page.get_by_role("menuitem", name="Delete").click(timeout=60000)
    await page.wait_for_timeout(500)

    await page.get_by_role("button", name="Delete").click(timeout=60000)
    await page.wait_for_timeout(1000)

    await page.locator("button").filter(has_text="Link").click(timeout=60000)
    await page.wait_for_timeout(500)

    link_input = page.get_by_role("textbox", name="Type or paste URL")
    await link_input.click(timeout=60000)
    await link_input.fill(new_link)

    await page.wait_for_timeout(3000)

    await page.get_by_role("button", name="Update").click(timeout=60000)
    await page.wait_for_timeout(2000)
