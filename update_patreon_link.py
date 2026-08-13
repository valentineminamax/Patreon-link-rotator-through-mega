"""
Rotates the MEGA link and updates an existing Patreon post's body text
in place (no delete/recreate, so likes/comments are preserved).

Uses Playwright with your existing saved session (patreon.json) instead of
re-authenticating. Locators are ACCESSIBILITY-TREE based (get_by_role /
get_by_label), not CSS selectors -- Patreon's React build regenerates class
names on every deploy, but role/accessible-name are stable because they
come from the DOM's ARIA semantics, not from build output.

Speed: unneeded resource types (images, fonts, media, stylesheets) and
known analytics/ad hosts are aborted at the network layer so the editor
page loads with only what's needed to interact with it.
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
    "SESSION_PATH": "patreon.json",   # your existing saved session

    # POST_ID comes from the PATREON_POST_ID secret in CI; falls back to
    # this literal for local/manual runs.
    "POST_ID": os.environ.get("PATREON_POST_ID", "123456789"),
    "EDIT_URL": "https://www.patreon.com/posts/{post_id}/edit",
    "PUBLIC_URL": "https://www.patreon.com/posts/{post_id}",

    "MEGA_LINK_PATTERN": r"https://mega\.nz/folder/[A-Za-z0-9#_-]+",

    # --- Locators: fill these in from `playwright codegen` output ---
    # (role, accessible_name_regex_or_None) pairs. Run codegen as shown
    # above and paste in whatever it records for each element.
    "LOCATOR_EDITOR_BODY":  {"role": "textbox", "name": re.compile(r".*", re.I)},   # PLACEHOLDER
    "LOCATOR_SAVE_BUTTON":  {"role": "button",  "name": re.compile(r"save", re.I)}, # PLACEHOLDER
    "LOCATOR_CONFIRMATION": {"role": "status",  "name": re.compile(r".*saved.*", re.I)}, # PLACEHOLDER

    # Resource types blocked outright for speed -- none of these are
    # needed to read/edit post text.
    "BLOCK_RESOURCE_TYPES": {"image", "media", "font", "stylesheet"},
    # Known non-essential hosts blocked outright (analytics/ads/tracking).
    # Add to this list as you spot slow third-party calls in devtools.
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
    """Abort heavy/unneeded requests so the editor loads with minimal wait."""
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
            body = page.get_by_role(loc["role"], name=loc["name"])
            body.wait_for(state="visible")

            current_text = body.inner_text()
            if not re.search(CONFIG["MEGA_LINK_PATTERN"], current_text):
                raise RuntimeError(
                    "No existing MEGA link found in post body via "
                    "MEGA_LINK_PATTERN -- refusing to guess. Check the "
                    "post/pattern manually."
                )
            new_text = re.sub(CONFIG["MEGA_LINK_PATTERN"], new_link, current_text)

            log.info("Step C: replacing link text ...")
            body.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(new_text, delay=5)

            log.info("Step D: saving ...")
            save_loc = CONFIG["LOCATOR_SAVE_BUTTON"]
            page.get_by_role(save_loc["role"], name=save_loc["name"]).click()

            conf_loc = CONFIG["LOCATOR_CONFIRMATION"]
            page.get_by_role(conf_loc["role"], name=conf_loc["name"]).wait_for(state="visible")
            log.info("Step D: save confirmed.")

            log.info("Step E: verifying live post at %s ...", public_url)
            page.goto(public_url, wait_until="domcontentloaded")
            if new_link not in page.content():
                raise RuntimeError(
                    "Verification failed: new link not present on live "
                    "post. NOT cleaning up old MEGA folder."
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
# Getting real role/name values (replace the PLACEHOLDER lines above)
# ======================================================================
#   playwright codegen https://www.patreon.com/posts/<POST_ID>/edit \
#       --load-storage=patreon.json
#
# Click the post body, the Save button, and (after a manual save) the
# success toast, in the recorder window that opens. Copy the role +
# name it prints for each into LOCATOR_EDITOR_BODY / LOCATOR_SAVE_BUTTON /
# LOCATOR_CONFIRMATION. These stay valid across React re-renders because
# they read the accessibility tree, not generated class names.
# ======================================================================
