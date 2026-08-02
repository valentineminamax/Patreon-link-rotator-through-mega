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
        # NOTE: previously used SELECTORS["block_menu_button"], a raw
        # "#_r_i7_" React useId selector. useId values are assigned by
        # render order, not tied to page content -- they are NOT
        # guaranteed to stay the same across reloads, unlike a proper
        # accessible-role selector. Replace the TODO below with what
        # `playwright codegen https://www.patreon.com/posts/xxxxxxx`
        # prints when you click that same button on the real post
        # (it will usually look like `get_by_role("button", name="...")`).
        page.get_by_role("button", name="TODO_more_options_label").click()
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
        # NOTE: previously used SELECTORS["link_input_field"], a raw
        # "#_r_g6_" React useId selector -- same fragility issue as
        # above. Replace the TODO with what codegen shows for this
        # field (likely `get_by_placeholder(...)` or
        # `get_by_role("textbox", name="...")`).
        link_input = page.get_by_placeholder("TODO_link_field_placeholder")
        link_input.click()
        link_input.fill(new_link)

        # 7. Give Patreon time to fetch a preview before saving
        page.wait_for_timeout(3000)

        # 8. Save
        page.click(SELECTORS["update_button"])
        page.wait_for_timeout(2000)

        browser.close()
