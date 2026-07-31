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

Selectors are pulled from config.py. If this stops working, the
classnames with random-looking hashes (Button-module__XXXXXX__...)
are the most likely culprit — Patreon regenerates those on redeploys.
Recapture fresh ones with `playwright codegen https://www.patreon.com`.
"""

import os

from playwright.sync_api import sync_playwright

from config import PATREON_POST_URL, SELECTORS

STORAGE_STATE_PATH = "patreon_session.json"


def update_patreon_link(new_link: str) -> None:
    if not os.path.exists(STORAGE_STATE_PATH):
        raise FileNotFoundError(
            f"{STORAGE_STATE_PATH} not found. Run login_once.py locally first, "
            "then set the PATREON_SESSION_JSON secret in your repo."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STORAGE_STATE_PATH)
        page = context.new_page()

        page.goto(PATREON_POST_URL)

        # 1. Open the post editor
        page.click(SELECTORS["edit_button"])
        page.wait_for_timeout(1500)

        # 2. Open the 3-dot menu on the existing link block
        page.click(SELECTORS["block_menu_button"])
        page.wait_for_timeout(500)

        # 3. Click "Delete"
        page.click(SELECTORS["delete_option"])
        page.wait_for_timeout(500)

        # 4. Confirm the deletion popup
        page.click(SELECTORS["confirm_delete_button"])
        page.wait_for_timeout(1000)

        # 5. Choose "Link" from the add-content picker
        page.click(SELECTORS["link_option_button"])
        page.wait_for_timeout(500)

        # 6. Type in the new link
        page.click(SELECTORS["link_input_field"])
        page.fill(SELECTORS["link_input_field"], new_link)

        # 7. Give Patreon time to fetch a preview before saving
        page.wait_for_timeout(3000)

        # 8. Save
        page.click(SELECTORS["update_button"])
        page.wait_for_timeout(2000)

        browser.close()
