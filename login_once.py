"""
Run this ONCE, locally, on your own machine (not in CI).

Opens a real browser window so you can log into Patreon yourself,
including completing 2FA. Once you're in, it saves the session
(cookies + local storage) to patreon_session.json, which the bot
reuses on every scheduled run so it never has to log in again.

Usage:
    pip install playwright
    playwright install chromium
    python login_once.py
"""

from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.patreon.com/login")

        print("Log in manually in the browser window that just opened.")
        print("Complete 2FA if prompted, and make sure you land on your dashboard.")
        input("Press Enter here once you're fully logged in...")

        context.storage_state(path="patreon_session.json")
        print("Saved session to patreon_session.json")
        print("Copy the CONTENTS of that file into the PATREON_SESSION_JSON GitHub secret.")

        browser.close()


if __name__ == "__main__":
    main()
