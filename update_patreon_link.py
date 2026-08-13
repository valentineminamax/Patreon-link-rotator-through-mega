"""
Rotates the MEGA link and updates an existing Patreon post's body text
in place (no delete/recreate, so likes/comments are preserved).

Uses Playwright with a saved session (patreon.json).
Locators are ACCESSIBILITY‑TREE based (get_by_role) – stable across React builds.

Direct DOM manipulation via evaluate() sets the new content and fires an input event
to ensure React picks up the change.
"""

import logging
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from mega_rotate import rotate_mega_link

# ============================== CONFIG ==============================

CONFIG = {
    "SESSION_PATH": "patreon.json",

    "POST_ID": os.environ.get("PATREON_POST_ID", "123456789"),
    "EDIT_URL": "https://www.patreon.com/posts/{post_id}/edit",
    "PUBLIC_URL": "https://www.patreon.com/posts/{post_id}",

    "MEGA_LINK_PATTERN": r"https://mega\.nz/folder/[A-Za-z0-9#_-]+",

    # --- Locators: fill these in from `playwright codegen` ---
    # Run:
    #   playwright codegen https://www.patreon.com/posts/<POST_ID>/edit --load-storage=patreon.json
    # Click the editor body, the Save button, and the success toast.
    # Copy the role and accessible name it shows for each.
    "LOCATOR_EDITOR_BODY":  {"role": "textbox", "name": re.compile(r".*", re.I)},   # PLACEHOLDER
    "LOCATOR_SAVE_BUTTON":  {"role": "button",  "name": re.compile(r"save", re.I)}, # PLACEHOLDER
    "LOCATOR_CONFIRMATION": {"role": "status",  "name": re.compile(r".*saved.*", re.I)}, # PLACEHOLDER

    # Resource types and hosts to abort for speed
    "BLOCK_RESOURCE_TYPES": {"image", "media", "font", "stylesheet"},
    "BLOCK_HOST_SUBSTRINGS": [
        "google-analytics.com", "googletagmanager.com", "doubleclick.net",
        "facebook.net", "connect.facebook.com", "segment.io", "segment.com",
        "sentry.io", "fullstory.com", "hotjar.com", "intercom.io",
    ],

    "HEADLESS": True,
    "NAV_TIMEOUT_MS": 20_000,
    "ACTION_TIMEOUT_MS": 10_000,
}

# ======================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("patreon_update")

def _install_fast_routing(page):
    """Abort heavy/unneeded requests."""
    block_types = CONFIG["BLOCK_RESOURCE_TYPES"]
    block_hosts = CONFIG["BLOCK_HOST_SUBSTRINGS"]

    def handler(route):
        req = route.request
        if req.resource_type in block_types:
            return route.abort()
        if any(h in req.url for h in block_hosts):
            return route.abort()
        return route.continue_()

    page.route("**/*", handler)

def update_patreon_post(new_link: str) -> None:
    session_path = Path(CONFIG["SESSION_PATH"])
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    edit_url = CONFIG["EDIT_URL"].format(post_id=CONFIG["POST_ID"])
    public_url = CONFIG["PUBLIC_URL"].format(post_id=CONFIG["POST_ID"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=CONFIG["HEADLESS"])
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()
        page.set_default_timeout(CONFIG["ACTION_TIMEOUT_MS"])
        page.set_default_navigation_timeout(CONFIG["NAV_TIMEOUT_MS"])
        _install_fast_routing(page)

        try:
            log.info("Step A: opening editor %s", edit_url)
            page.goto(edit_url, wait_until="domcontentloaded")

            log.info("Step B: locating editor body via role ...")
            loc = CONFIG["LOCATOR_EDITOR_BODY"]
            editor = page.get_by_role(loc["role"], name=loc["name"])
            editor.wait_for(state="visible")

            # 1. Get current content (HTML for contenteditable, value for textarea)
            current_html = editor.evaluate("el => el.innerHTML")  # works for both
            if not re.search(CONFIG["MEGA_LINK_PATTERN"], current_html):
                raise RuntimeError(
                    "No existing MEGA link found in post body. Check pattern or post content."
                )

            new_html = re.sub(CONFIG["MEGA_LINK_PATTERN"], new_link, current_html)

            log.info("Step C: replacing link directly via evaluate() ...")
            # Set the new content and trigger an input event so React picks it up
            editor.evaluate(f"""
                (el) => {{
                    el.innerHTML = `{new_html}`;
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            """)

            # Optional: wait a moment for React to settle
            page.wait_for_timeout(500)

            log.info("Step D: saving ...")
            save_loc = CONFIG["LOCATOR_SAVE_BUTTON"]
            page.get_by_role(save_loc["role"], name=save_loc["name"]).click()

            # Wait for the confirmation toast / status message
            conf_loc = CONFIG["LOCATOR_CONFIRMATION"]
            page.get_by_role(conf_loc["role"], name=conf_loc["name"]).wait_for(state="visible")
            log.info("Step D: save confirmed.")

            log.info("Step E: verifying live post at %s ...", public_url)
            page.goto(public_url, wait_until="domcontentloaded")
            # Wait for the post content to load (body might be lazy)
            page.wait_for_selector("article", timeout=5000).or_(page.wait_for_selector("div[data-testid='post-content']"))
            page_content = page.content()
            if new_link not in page_content:
                raise RuntimeError(
                    "Verification failed: new link not present on live post. "
                    "NOT cleaning up old MEGA folder."
                )
            log.info("Step E: verified.")

        except PWTimeout as e:
            raise RuntimeError(f"Timed out waiting on a locator: {e}") from e
        finally:
            context.close()
            browser.close()

def main():
    log.info("=== Starting MEGA + Patreon link rotation ===")
    new_link, old_path, old_name, temp_path = rotate_mega_link()
    log.info("New MEGA link ready: %s", new_link)

    try:
        update_patreon_post(new_link)
    except Exception:
        log.exception("Patreon update failed -- rolling back temp folder %s", temp_path)
        _rollback(temp_path)
        raise

    log.info("Verified. Cleaning up old folder %s", old_path)
    _cleanup_old(old_path)
    log.info("=== Done. Old export at %s disabled. ===", old_name)

def _rollback(temp_path: str) -> None:
    import subprocess
    subprocess.run(["mega-rm", "-r", temp_path], capture_output=True, text=True)

def _cleanup_old(old_path: str) -> None:
    import subprocess
    subprocess.run(["mega-export", "-d", old_path], capture_output=True, text=True)
    subprocess.run(["mega-rm", "-r", old_path], capture_output=True, text=True)

if __name__ == "__main__":
    main()

# ======================================================================
# How to get real locator values:
#   playwright codegen https://www.patreon.com/posts/<POST_ID>/edit \
#       --load-storage=patreon.json
# Click the editor, Save button, and success toast.
# Copy the role + name that appear in the recorder.
# ======================================================================
