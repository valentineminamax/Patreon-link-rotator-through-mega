"""
Opens your Patreon post editor (using a saved logged-in session)
and swaps in the new MEGA link.

Selectors are pulled from config.py — update those if Patreon
changes their editor's DOM and the bot starts failing.
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
        page.click(SELECTORS["edit_button"])
        page.wait_for_selector(SELECTORS["body_textbox"])

        page.click(SELECTORS["body_textbox"])
        page.keyboard.press("Control+A")
        page.keyboard.type(new_link)

        page.click(SELECTORS["save_button"])
        page.wait_for_timeout(2000)  # let the save request land

        browser.close()
