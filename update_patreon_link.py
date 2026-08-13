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
    "NAV_TIMEOUT_MS": 90000,
    "ACTION_TIMEOUT_MS": 60000,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("patreon_update")


def _get_proxy_config() -> Optional[dict]:
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
    log.warning("No proxy configured – using direct connection.")
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
    After insertion, it saves the post and waits 5 seconds.
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
    # Click the link insertion button (toolbar)
    link_button = page.locator(CONFIG["LOCATOR_LINK_BUTTON_SELECTOR"])
    if await link_button.count() == 0:
        # Fallback to old selector
        link_button = page.locator("button:has-text('Link')")
    await link_button.click()

    # Fill the URL
    await page.get_by_role(
        CONFIG["LOCATOR_LINK_INPUT"]["role"],
        name=CONFIG["LOCATOR_LINK_INPUT"]["name"]
    ).fill(new_link)

    # Click the "Update" button inside the modal (fetches embed)
    await page.get_by_role(
        CONFIG["LOCATOR_UPDATE_BUTTON"]["role"],
        name=CONFIG["LOCATOR_UPDATE_BUTTON"]["name"]
    ).click()

    # Wait for the modal to either close or show an "Insert" button
    log.info("Waiting for link embed to load and modal to close...")
    try:
        # Wait for the textbox to become detached (modal closed)
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

    # ---- Improved link presence check ----
    log.info("Verifying link presence in editor (using text selector)...")
    try:
        # Try to locate the link by text (regex) and wait for it to be attached.
        link_locator = page.get_by_text(re.compile(CONFIG["MEGA_LINK_PATTERN"])).first
        await link_locator.wait_for(state="attached", timeout=30000)
        log.info("Link found via text selector.")
    except Exception:
        # Fallback: check for an <a> tag whose href contains the link pattern
        log.warning("Link not found via text selector, checking for anchor element...")
        try:
            href_locator = page.locator(f'a[href*="{new_link[:50]}"]')  # partial match
            await href_locator.first.wait_for(state="attached", timeout=10000)
            log.info("Link found via href selector.")
        except Exception:
            # Ultimate fallback: check if the full URL appears in the page HTML
            log.warning("Link not found via href selector, checking page content...")
            await asyncio.sleep(2)  # give it a final moment
            content = await page.content()
            if new_link in content:
                log.info("Link found in page content after all.")
            else:
                # Capture failure artifacts
                await page.screenshot(path="patreon_link_not_inserted.png", full_page=True)
                with open("patreon_link_not_inserted.html", "w", encoding="utf-8") as f:
                    f.write(content)
                raise RuntimeError(
                    "Link does not appear in the editor after insertion. "
                    "See patreon_link_not_inserted.png / patreon_link_not_inserted.html "
                    "for the exact editor state."
                )

    # Now save the post (click the main "Save" or "Update" button)
    log.info("Saving the post...")
    save_button = page.locator('button[data-tag="make-a-post-action-save"]')
    await save_button.click()

    # Wait for save to complete (button no longer loading)
    try:
        await page.wait_for_function(
            """() => {
                const b = document.querySelector('button[data-tag="make-a-post-action-save"]');
                return !b || !b.className.includes('isLoading');
            }""",
            timeout=30000,
        )
        log.info("Post saved successfully.")
    except Exception as e:
        log.warning("Save completion wait timed out: %s", e)

    # Additional 5‑second wait to ensure backend propagation
    log.info("Waiting 5 seconds for changes to propagate...")
    await asyncio.sleep(5)


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

            log.info("Verifying public post: %s", public_url)
            await page.goto(public_url, wait_until="domcontentloaded")
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
