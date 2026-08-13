import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from patchright.async_api import async_playwright, Page
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
from mega_rotate import rotate_mega_link, finalize_active_folder

# =========================== CONFIG ===========================
CONFIG = {
    "SESSION_PATH": "patreon_session.json",
    "POST_ID": os.environ.get("PATREON_POST_ID", "123456789"),
    "EDIT_URL": "https://www.patreon.com/posts/{post_id}/edit",
    "PUBLIC_URL": "https://www.patreon.com/posts/{post_id}",

    "MEGA_LINK_PATTERN": r"https://mega\.nz/folder/[A-Za-z0-9#_-]+",

    "LOCATOR_KEBAB_BUTTON": {"role": "button", "name": "Menu for additional actions"},
    "LOCATOR_DELETE_ITEM": {"role": "menuitem", "name": "Delete"},
    "LOCATOR_CONFIRM_DELETE": {"role": "button", "name": "Delete"},
    "LOCATOR_LINK_BUTTON_SELECTOR": "button[aria-label='Insert link']",
    "LOCATOR_LINK_INPUT": {"role": "textbox", "name": "Type or paste URL"},
    "LOCATOR_UPDATE_BUTTON": {"role": "button", "name": "Update"},
    "LOCATOR_INSERT_BUTTON": {"role": "button", "name": "Insert"},

    "HEADLESS": True,
    "NAV_TIMEOUT_MS": 120000,
    "ACTION_TIMEOUT_MS": 120000,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("patreon_update")


def _get_proxy_config() -> Optional[dict]:
    # Only use proxy if explicitly enabled via environment variable
    use_proxy = os.getenv("USE_PROXY", "false").lower() in ("true", "1", "yes")
    if not use_proxy:
        log.info("Proxy disabled by USE_PROXY setting – using direct connection.")
        return None

    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")
    if host and port:
        proxy = {"server": f"http://{host}:{port}"}
        if username and password:
            proxy["username"] = username
            proxy["password"] = password
        log.info(f"Using proxy: {proxy['server']}")
        return proxy
    log.warning("Proxy enabled but missing host/port – falling back to direct connection.")
    return None


async def _handle_cloudflare(page: Page) -> None:
    title = await page.title()
    if "Just a moment..." in title or "Checking your browser" in title:
        log.info("Cloudflare challenge detected. Attempting bypass...")
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
                    log.warning("Turnstile solver failed, trying Interstitial...")
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                    )
            log.info("Cloudflare challenge solved.")
            await page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            await page.screenshot(path="cloudflare_failure.png", full_page=True)
            with open("cloudflare_failure.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise RuntimeError(f"Cloudflare bypass failed: {e}")


async def _replace_link_in_editor(page: Page, new_link: str) -> None:
    """
    Replaces the existing MEGA link with the new one.
    If no existing link is found, skip deletion and just add a new link.
    After insertion, it saves the post and waits for navigation to the public page.
    """
    # Locate existing link
    try:
        link_element = page.get_by_text(re.compile(CONFIG["MEGA_LINK_PATTERN"])).first
        await link_element.wait_for(state="attached", timeout=2000)
        has_link = True
    except Exception:
        has_link = False

    if has_link:
        log.info("Existing MEGA link found. Deleting it...")
        await page.get_by_role(
            CONFIG["LOCATOR_KEBAB_BUTTON"]["role"],
            name=CONFIG["LOCATOR_KEBAB_BUTTON"]["name"]
        ).click()
        await page.get_by_role(
            CONFIG["LOCATOR_DELETE_ITEM"]["role"],
            name=CONFIG["LOCATOR_DELETE_ITEM"]["name"]
        ).click()
        await page.get_by_role(
            CONFIG["LOCATOR_CONFIRM_DELETE"]["role"],
            name=CONFIG["LOCATOR_CONFIRM_DELETE"]["name"]
        ).click()
        log.info("Link deleted.")
    else:
        log.info("No existing MEGA link found. Skipping deletion.")

    # Insert new link
    log.info("Adding new link...")
    link_button = page.locator(CONFIG["LOCATOR_LINK_BUTTON_SELECTOR"])
    if await link_button.count() == 0:
        link_button = page.locator("button:has-text('Link')")
    await link_button.click()

    await page.get_by_role(
        CONFIG["LOCATOR_LINK_INPUT"]["role"],
        name=CONFIG["LOCATOR_LINK_INPUT"]["name"]
    ).fill(new_link)

    await page.get_by_role(
        CONFIG["LOCATOR_UPDATE_BUTTON"]["role"],
        name=CONFIG["LOCATOR_UPDATE_BUTTON"]["name"]
    ).click()

    log.info("Waiting for link embed to load and modal to close...")
    try:
        await page.get_by_role(
            CONFIG["LOCATOR_LINK_INPUT"]["role"],
            name=CONFIG["LOCATOR_LINK_INPUT"]["name"]
        ).wait_for(state="detached", timeout=15000)
        log.info("Modal closed automatically.")
    except Exception:
        log.warning("Modal did not close automatically. Looking for 'Insert' button...")
        insert_btn = page.get_by_role("button", name=re.compile(r"Insert|Add|Done", re.I))
        if await insert_btn.count() > 0:
            await insert_btn.click()
            log.info("Clicked 'Insert' button.")
        else:
            log.warning("No 'Insert' button found. Assuming link was inserted anyway.")

    # Verify link presence
    log.info("Verifying link presence in editor (using text selector)...")
    try:
        link_locator = page.get_by_text(re.compile(CONFIG["MEGA_LINK_PATTERN"])).first
        await link_locator.wait_for(state="attached", timeout=30000)
        log.info("Link found via text selector.")
    except Exception:
        log.warning("Link not found via text selector, checking for anchor element...")
        try:
            href_locator = page.locator(f'a[href*="{new_link[:50]}"]')
            await href_locator.first.wait_for(state="attached", timeout=10000)
            log.info("Link found via href selector.")
        except Exception:
            log.warning("Link not found via href selector, checking page content...")
            await asyncio.sleep(2)
            content = await page.content()
            if new_link in content:
                log.info("Link found in page content after all.")
            else:
                await page.screenshot(path="patreon_link_not_inserted.png", full_page=True)
                with open("patreon_link_not_inserted.html", "w", encoding="utf-8") as f:
                    f.write(content)
                raise RuntimeError(
                    "Link does not appear in the editor after insertion. "
                    "See patreon_link_not_inserted.png / patreon_link_not_inserted.html "
                    "for the exact editor state."
                )

    # Save the post and wait for navigation to the public page
    log.info("Saving the post...")
    save_button = page.locator('button[data-tag="make-a-post-action-save"]')
    # Click save and wait for URL to change (remove /edit)
    async with page.context.expect_page() as new_page_info:
        await save_button.click()
        try:
            # Wait for the URL to no longer contain "/edit"
            await page.wait_for_url(lambda url: "/edit" not in url, timeout=60000)
            log.info("Page navigated to public view.")
        except Exception:
            # If no navigation, check if a new tab opened
            new_page = await new_page_info.value
            if new_page:
                log.info("Post opened in new tab.")
                await new_page.wait_for_load_state("networkidle", timeout=60000)
                await page.close()
                page = new_page
            else:
                log.warning("No navigation detected. Falling back to manual navigation.")
                # Manual navigation will be done in the caller

    await asyncio.sleep(2)


async def update_patreon_post(new_link: str) -> None:
    session_path = Path(CONFIG["SESSION_PATH"])
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    edit_url = CONFIG["EDIT_URL"].format(post_id=CONFIG["POST_ID"])
    public_url = CONFIG["PUBLIC_URL"].format(post_id=CONFIG["POST_ID"])
    proxy_config = _get_proxy_config()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=CONFIG["HEADLESS"],
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
            storage_state=str(session_path),
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
        page.set_default_timeout(CONFIG["ACTION_TIMEOUT_MS"])
        page.set_default_navigation_timeout(CONFIG["NAV_TIMEOUT_MS"])

        try:
            log.info("Opening editor: %s", edit_url)
            await page.goto(edit_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            await _handle_cloudflare(page)
            await _replace_link_in_editor(page, new_link)

            # If we're still on the edit page, manually navigate to public with retries
            current_url = page.url
            if "/edit" in current_url:
                log.info("Still on edit page, manually navigating to public...")
                for attempt in range(3):
                    try:
                        await page.goto(public_url, wait_until="domcontentloaded", timeout=90000)
                        break
                    except Exception as e:
                        log.warning(f"Navigation attempt {attempt+1} failed: {e}. Retrying...")
                        await asyncio.sleep(5)
                else:
                    raise RuntimeError("Failed to navigate to public post after 3 attempts.")
            else:
                log.info("Already on public page.")

            await _handle_cloudflare(page)
            log.info("Public post page loaded (title: %s)", await page.title())
            content = await page.content()
            if new_link not in content:
                raise RuntimeError(
                    "Verification failed: new link not found on live post. "
                    "NOT cleaning up old MEGA folder."
                )
            log.info("Verification passed – new link is live.")

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


def _disable_and_remove_old(old_path: str) -> None:
    import subprocess
    log.info("Disabling old export and removing folder: %s", old_path)
    try:
        subprocess.run(
            ["mega-export", "-d", old_path],
            input="yes\n", capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        log.warning("Could not disable export on %s (may already be disabled): %s", old_path, e)

    try:
        subprocess.run(
            ["mega-mv", old_path, "//bin/"],
            capture_output=True, text=True, timeout=20,
        )
        log.info("Old folder moved to Rubbish Bin: %s", old_path)
        return
    except Exception as e:
        log.warning("Move-to-bin failed for %s (%s), falling back to mega-rm -r", old_path, e)
    try:
        subprocess.run(
            ["mega-rm", "-r", old_path],
            capture_output=True, text=True, timeout=90,
        )
    except Exception as e:
        log.warning("Could not remove old folder %s: %s", old_path, e)


def _rollback_sync(temp_path: str) -> None:
    import subprocess
    log.info("Rolling back: moving temp folder to bin: %s", temp_path)
    try:
        subprocess.run(["mega-mv", temp_path, "//bin/"], capture_output=True, text=True, timeout=20)
        return
    except Exception as e:
        log.warning("Move-to-bin failed for %s (%s), falling back to mega-rm -r", temp_path, e)
    try:
        subprocess.run(["mega-rm", "-r", temp_path], capture_output=True, text=True, timeout=90)
    except Exception as e:
        log.warning("Could not remove temp folder %s: %s", temp_path, e)


async def main():
    log.info("=== Starting MEGA + Patreon link rotation ===")
    loop = asyncio.get_running_loop()
    new_link, old_path, temp_path = await loop.run_in_executor(
        None, rotate_mega_link
    )
    log.info("New MEGA link ready: %s", new_link)
    log.info("Old folder (still active): %s", old_path)
    log.info("Temp folder (to keep if update succeeds, delete if fails): %s", temp_path)

    try:
        await update_patreon_post(new_link)
    except Exception:
        log.exception("Patreon update failed – rolling back (deleting temp folder)")
        await loop.run_in_executor(None, _rollback_sync, temp_path)
        raise

    log.info("Verified. Cleaning up old folder %s", old_path)
    await loop.run_in_executor(None, _disable_and_remove_old, old_path)

    final_path = await loop.run_in_executor(None, finalize_active_folder, temp_path)
    log.info("=== Done. Old folder removed. Active folder is now: %s ===", final_path)


if __name__ == "__main__":
    asyncio.run(main())
