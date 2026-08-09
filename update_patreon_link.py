import asyncio
import logging
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Optional

from patchright.async_api import async_playwright, Page
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
from mega_rotate import rotate_mega_link

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

STEP_TIMEOUT = 30
UPDATE_TIMEOUT = 300
MEGA_CMD_TIMEOUT = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("patreon_update")

# ========== PROXY FIX ==========
def _get_proxy_config() -> Optional[dict]:
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")
    
    if not username or not password:
        log.warning("No proxy credentials configured.")
        return None
    
    import time
    session_id = str(int(time.time()))
    proxy_username = f"{username};sessid.{session_id}"
    
    proxy_config = {
        "server": "http://gw.dataimpulse.com:823",
        "username": proxy_username,
        "password": password,
    }
    log.info(f"Using proxy: {proxy_config['server']} (sticky session)")
    return proxy_config

# ========== PROXY TEST (using Firefox) ==========
async def test_proxy():
    proxy_config = _get_proxy_config()
    if not proxy_config:
        log.warning("No proxy configured, skipping test.")
        return True
    
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context(proxy=proxy_config)
        page = await context.new_page()
        try:
            await page.goto("https://api.ipify.org", timeout=30000)
            ip = await page.text_content("body")
            log.info(f"✅ Proxy working! Your IP: {ip}")
            await browser.close()
            return True
        except Exception as e:
            log.error(f"❌ Proxy test failed: {e}")
            await browser.close()
            return False

# ========== CLOUDFLARE BYPASS ==========
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
                    await asyncio.wait_for(
                        solver.solve_captcha(
                            captcha_container=page,
                            captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE
                        ),
                        timeout=60
                    )
                except asyncio.TimeoutError:
                    log.warning("Turnstile solver timed out, trying Interstitial...")
                    await asyncio.wait_for(
                        solver.solve_captcha(
                            captcha_container=page,
                            captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                        ),
                        timeout=60
                    )
            log.info("Cloudflare challenge solved.")
            await page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            await page.screenshot(path="cloudflare_failure.png", full_page=True)
            with open("cloudflare_failure.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise RuntimeError(f"Cloudflare bypass failed: {e}")

# ========== PATREON EDITOR ACTIONS ==========
async def _replace_link_in_editor(page: Page, new_link: str) -> None:
    kebab_locator = page.get_by_role(
        CONFIG["LOCATOR_KEBAB_BUTTON"]["role"],
        name=CONFIG["LOCATOR_KEBAB_BUTTON"]["name"]
    )
    kebab_visible = False
    try:
        await asyncio.wait_for(kebab_locator.first.wait_for(state="visible", timeout=5), timeout=5)
        kebab_visible = True
        log.info("Old link block found – will delete it.")
    except:
        log.info("No old link block found – skipping deletion.")

    if kebab_visible:
        log.info("Step 1: Click kebab menu.")
        await asyncio.wait_for(kebab_locator.click(), timeout=STEP_TIMEOUT)

        log.info("Step 2: Click Delete in dropdown.")
        await asyncio.wait_for(
            page.get_by_role(
                CONFIG["LOCATOR_DELETE_ITEM"]["role"],
                name=CONFIG["LOCATOR_DELETE_ITEM"]["name"]
            ).click(),
            timeout=STEP_TIMEOUT
        )

        log.info("Step 3: Confirm delete.")
        await asyncio.wait_for(
            page.get_by_role(
                CONFIG["LOCATOR_CONFIRM_DELETE"]["role"],
                name=CONFIG["LOCATOR_CONFIRM_DELETE"]["name"]
            ).click(),
            timeout=STEP_TIMEOUT
        )
        await asyncio.sleep(0.5)

    log.info("Step 4: Click 'Link' button.")
    await asyncio.wait_for(
        page.locator(CONFIG["LOCATOR_LINK_BUTTON_SELECTOR"]).click(),
        timeout=STEP_TIMEOUT
    )

    log.info("Step 5: Fill new URL.")
    await asyncio.wait_for(
        page.get_by_role(
            CONFIG["LOCATOR_LINK_INPUT"]["role"],
            name=CONFIG["LOCATOR_LINK_INPUT"]["name"]
        ).fill(new_link),
        timeout=STEP_TIMEOUT
    )

    log.info("Step 6: Wait 3 seconds for link processing.")
    await asyncio.sleep(3)

    log.info("Step 7: Click 'Update' once to save.")
    await asyncio.wait_for(
        page.get_by_role(
            CONFIG["LOCATOR_UPDATE_BUTTON"]["role"],
            name=CONFIG["LOCATOR_UPDATE_BUTTON"]["name"]
        ).click(),
        timeout=STEP_TIMEOUT
    )

# ========== MAIN PATREON UPDATE ==========
async def update_patreon_post(new_link: str) -> None:
    session_path = Path(CONFIG["SESSION_PATH"])
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    edit_url = CONFIG["EDIT_URL"].format(post_id=CONFIG["POST_ID"])
    public_url = CONFIG["PUBLIC_URL"].format(post_id=CONFIG["POST_ID"])
    proxy_config = _get_proxy_config()

    if not await test_proxy():
        raise RuntimeError("Proxy test failed – check your credentials.")

    async with async_playwright() as p:
        browser = await p.firefox.launch(
            headless=CONFIG["HEADLESS"],
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
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
            await asyncio.wait_for(
                page.goto(edit_url, wait_until="domcontentloaded"),
                timeout=60
            )
            await asyncio.sleep(2)

            await _handle_cloudflare(page)

            await asyncio.wait_for(
                _replace_link_in_editor(page, new_link),
                timeout=UPDATE_TIMEOUT
            )

            log.info("Verifying public post: %s", public_url)
            await asyncio.wait_for(
                page.goto(public_url, wait_until="domcontentloaded"),
                timeout=60
            )
            content = await page.content()
            if new_link not in content:
                raise RuntimeError(
                    "Verification failed: new link not found on live post. "
                    "NOT cleaning up old MEGA folder."
                )
            log.info("Verification passed.")

        except asyncio.TimeoutError:
            log.error("Patreon update timed out after %s seconds.", UPDATE_TIMEOUT)
            await page.screenshot(path="patreon_timeout.png", full_page=True)
            with open("patreon_timeout.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise TimeoutError(f"Patreon update exceeded {UPDATE_TIMEOUT}s limit.")
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

# ========== MEGA HELPERS WITH NON‑BLOCKING DELETION ==========
def _folder_exists(path: str) -> bool:
    try:
        result = subprocess.run(
            ["mega-ls", path],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.warning(f"Timeout checking existence of {path} – assuming it doesn't exist.")
        return False

def _run_mega_cmd(cmd, timeout=MEGA_CMD_TIMEOUT):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            log.warning(f"Command failed: {' '.join(cmd)}")
            log.warning(f"stdout: {result.stdout}")
            log.warning(f"stderr: {result.stderr}")
        return result
    except subprocess.TimeoutExpired:
        log.warning(f"Command timed out after {timeout}s: {' '.join(cmd)}")
        class DummyResult:
            returncode = -1
            stdout = ""
            stderr = "Timeout"
        return DummyResult()

def _delete_mega_folder_safe(path: str) -> bool:
    if not _folder_exists(path):
        log.info(f"Folder {path} does not exist – skipping deletion.")
        return True

    log.info(f"Deleting folder: {path}")
    _run_mega_cmd(["mega-export", "-d", path])
    result = _run_mega_cmd(["mega-rm", "-r", path])
    if result.returncode == 0:
        log.info(f"Successfully deleted {path}")
        return True
    else:
        log.warning(f"Failed to delete {path} (returncode {result.returncode}) – will be cleaned up later.")
        return False

def _rollback_sync(temp_path: str) -> None:
    # Non‑critical: if it fails, we log and continue (the folder will be removed later)
    _delete_mega_folder_safe(temp_path)

def _cleanup_old_sync(old_path: str) -> None:
    # Same here – not critical if it fails
    _delete_mega_folder_safe(old_path)

# ========== MAIN ==========
async def main():
    log.info("=== Starting MEGA + Patreon link rotation ===")
    loop = asyncio.get_running_loop()

    try:
        result = await loop.run_in_executor(None, rotate_mega_link)
        if len(result) == 4:
            new_link, old_path, old_name, temp_path = result
        else:
            raise ValueError(f"Unexpected return values: {len(result)}")
    except Exception as e:
        log.error(f"Failed to rotate MEGA link: {e}")
        raise

    log.info("New MEGA link ready: %s", new_link)

    try:
        await update_patreon_post(new_link)
    except Exception:
        log.exception("Patreon update failed – rolling back temp folder")
        await loop.run_in_executor(None, _rollback_sync, temp_path)
        raise

    log.info("Patreon update verified. Now cleaning up old folder.")
    await loop.run_in_executor(None, _cleanup_old_sync, old_path)
    log.info("=== Done. Old export (if any) disabled and folder deletion attempted. ===")

if __name__ == "__main__":
    asyncio.run(main())
