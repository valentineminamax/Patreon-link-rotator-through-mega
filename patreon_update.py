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

Selectors used to be pulled from config.py's SELECTORS dict (raw CSS,
including some with random-looking hashes like Button-module__XXXXXX__
and some React useId values like #_r_g6_). All of those turned out to
be too fragile for real use -- 3 of 7 broke on the very first real
run. They've been replaced below with role-based Playwright locators
(get_by_role / get_by_placeholder), which are more resistant to
Patreon redeploys and DOM/render-order changes. Each one still has a
TODO placeholder for its real accessible name/placeholder text --
fill those in via `playwright codegen https://www.patreon.com/posts/xxxxxxx`
against the real post. config.py's SELECTORS dict is kept only as a
reference for what the old (broken) selectors looked like.
"""

import os

from playwright.sync_api import sync_playwright

from config import PATREON_POST_URL

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
        # NOTE: previously used SELECTORS["edit_button"], a long chain
        # of ":nth-child()" combined with a "Stack-module__Qen2pq__..."
        # hash classname. This is exactly the fragility flagged in the
        # original handoff notes -- it just failed in a real run
        # (30s timeout, no match found), most likely because Patreon
        # redeployed and the hash changed. Replace the TODO with what
        # `playwright codegen https://www.patreon.com/posts/xxxxxxx`
        # shows when you click the real "Edit" button on the post
        # (likely `get_by_role("button", name="Edit")` or similar).
        page.get_by_role("button", name="Edit").click()
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
        page.get_by_role("button", name="Menu for additional actions").click()
        page.wait_for_timeout(500)

        # 3. Click "Delete"
        # NOTE: previously SELECTORS["delete_option"], a generic
        # text-align utility class -- flagged from the start as risky
        # since it could match more than one menu item, and shares the
        # same "captured once, now stale" risk as the others.
        page.get_by_role("menuitem", name="Delete").click()
        page.wait_for_timeout(500)

        # 4. Confirm the deletion popup
        # NOTE: previously SELECTORS["confirm_delete_button"], a
        # Button-module__YRbc6a__... hash classname -- same hash family
        # as edit_button, which just broke in a real run.
        page.get_by_role("button", name="Delete").click()
        page.wait_for_timeout(1000)

        # 5. Choose "Link" from the add-content picker
        # NOTE: previously SELECTORS["link_option_button"], same hash
        # classname family as above.
        page.locator("button").filter(has_text="Link").click()
        page.wait_for_timeout(500)

        # 6. Type in the new link
        # NOTE: previously used SELECTORS["link_input_field"], a raw
        # "#_r_g6_" React useId selector -- same fragility issue as
        # above. Replace the TODO with what codegen shows for this
        # field (likely `get_by_placeholder(...)` or
        # `get_by_role("textbox", name="...")`).
        link_input = page.get_by_role("textbox", name="Type or paste URL")
        link_input.click()
        link_input.fill(new_link)

        # 7. Give Patreon time to fetch a preview before saving
        page.wait_for_timeout(3000)

        # 8. Save
        # NOTE: previously SELECTORS["update_button"], same hash
        # classname family as above.
        page.get_by_role("button", name="Update").click()
        page.wait_for_timeout(2000)

        browser.close()
