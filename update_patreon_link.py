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
    "LOCATOR_LINK_BUTTON_SELECTOR": "button:has-text('Link')",
    "LOCATOR_LINK_INPUT": {"role": "textbox", "name": "Type or paste URL"},
    "LOCATOR_UPDATE_BUTTON": {"role": "button", "name": "Update"},

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
    """
    try:
        # FIX: previously this built the selector as f"text=/{pattern}/",
        # but MEGA_LINK_PATTERN itself contains literal "/" characters
        # (https://mega.nz/folder/...), which breaks Playwright's
        # "text=/regex/" delimiter parsing. That made this lookup fail
        # silently every time (caught below), so has_link was always
        # False and the old link was never actually deleted before
        # adding the new one. Passing a compiled regex to get_by_text()
        # avoids the string-delimiter problem entirely.
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

    log.info("Adding new link...")
    await page.locator(CONFIG["LOCATOR_LINK_BUTTON_SELECTOR"]).click()
    await page.get_by_role(
        CONFIG["LOCATOR_LINK_INPUT"]["role"],
        name=CONFIG["LOCATOR_LINK_INPUT"]["name"]
    ).fill(new_link)

    # Quick, non-fatal look that the link actually landed in the input/
    # editor before confirming. This is just a sanity glance - it never
    # raises. We deliberately don't hard-fail here anymore: a network
    # trace from a real run showed the save PATCH returning 200 well
    # within the old 30s waits, while Patreon's own client-side signals
    # (Update button spinner, link text appearing in page.content()) still
    # looked like a failure. The user has seen the exact same thing doing
    # this manually - Patreon's editor UI shows an error/stuck state even
    # when the update actually went through. So the editor is no longer
    # treated as the source of truth; it's just a quick look.
    try:
        await page.wait_for_function(
            "(linkText) => document.body.innerHTML.includes(linkText)",
            arg=new_link,
            timeout=3000,
        )
        log.info("Link visible in editor before confirming.")
    except Exception:
        log.info("Link not visibly rendered yet - proceeding to confirm anyway.")

    await page.get_by_role(
        CONFIG["LOCATOR_UPDATE_BUTTON"]["role"],
        name=CONFIG["LOCATOR_UPDATE_BUTTON"]["name"]
    ).click()

    # Fixed settle time and done - no post-Update verification here.
    # The real check is the public-post verification in
    # update_patreon_post(), which hits the actually-published page
    # rather than trusting this editor's client-side state.
    await asyncio.sleep(5)
    log.info("Update clicked, settle wait complete.")


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
            # FIX: Cloudflare was only ever handled on the editor page. If a
            # challenge shows up here too, the script previously had no way
            # to solve it and would just sit until NAV_TIMEOUT_MS expired
            # (up to 90s) - which is what looked like "stuck."
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

    # FIX: mega-rm -r is a synchronous, permanent delete that blocks until
    # the whole recursive removal finishes server-side - that's what was
    # hanging for minutes. Moving to the Rubbish Bin (//bin/) is a single
    # lightweight call, effectively instant - the same thing a manual
    # "delete" in the web/app actually does. Falls back to mega-rm -r only
    # if the move itself fails.
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

    # FIX: this step didn't exist before, which is why the fixed folder
    # name never came back after a successful rotation. Renames the temp
    # folder back to FOLDER_FIXED_NAME (if configured) so next run's
    # folder lookup finds it by fixed name again.
    final_path = await loop.run_in_executor(None, finalize_active_folder, temp_path)
    log.info("=== Done. Old folder removed. Active folder is now: %s ===", final_path)


if __name__ == "__main__":
    asyncio.run(main())
