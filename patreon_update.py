"""
Swaps the MEGA link inside a Patreon post's link block using Playwright,
driven by role-based selectors (Patreon's DOM is React-generated and class
names aren't stable, so role/name locators are the reliable anchor).

Behavior:
- If a link block already exists (detected via the "Menu for additional
  actions" button on that block), it is deleted first via the
  menu -> Delete -> confirm Delete flow.
- If no link block exists yet, deletion is skipped entirely and a new
  Link block is added directly - so a first run, or a post someone already
  stripped, doesn't error out or click things that aren't there.
- On any failure, a full-page screenshot and the page's HTML are dumped to
  `screenshot_dir` before re-raising, so the GitHub Actions workflow can
  upload them as evidence.
- The proxy (if configured) is applied ONLY to this Chromium instance -
  it's the only traffic in the whole run that needs to look residential.
"""

import json
import os

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import config

# How long to wait to see if a link block's menu button is present before
# concluding there's no existing link to delete.
LINK_DETECT_TIMEOUT_MS = 5000
POST_UPDATE_SETTLE_MS = 5000


def _proxy_settings():
    if not (config.PROXY_HOST and config.PROXY_PORT):
        return None
    proxy = {"server": f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"}
    if config.PROXY_USERNAME:
        proxy["username"] = config.PROXY_USERNAME
    if config.PROXY_PASSWORD:
        proxy["password"] = config.PROXY_PASSWORD
    return proxy


def _dump_debug(page, screenshot_dir: str, name: str) -> None:
    os.makedirs(screenshot_dir, exist_ok=True)

    png_path = os.path.join(screenshot_dir, f"{name}.png")
    try:
        page.screenshot(path=png_path, full_page=True)
        print(f"Saved debug screenshot: {png_path}", flush=True)
    except Exception as e:
        print(f"Note: could not save screenshot: {e}", flush=True)

    html_path = os.path.join(screenshot_dir, f"{name}.html")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"Saved debug HTML: {html_path}", flush=True)
    except Exception as e:
        print(f"Note: could not save page HTML: {e}", flush=True)


def update_patreon_link(new_link: str, screenshot_dir: str = "artifacts") -> None:
    """
    Opens config.PATREON_POST_URL in edit mode and replaces the MEGA link
    with `new_link`. Raises on failure (after saving debug evidence).
    """
    storage_state = json.loads(config.PATREON_SESSION_JSON)
    proxy = _proxy_settings()

    with sync_playwright() as playwright:
        launch_kwargs = {"headless": True}
        if proxy:
            launch_kwargs["proxy"] = proxy
            print("Launching Chromium with proxy for the Patreon session.", flush=True)
        else:
            print("No proxy configured - launching Chromium directly.", flush=True)

        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()

        try:
            print(f"Opening post editor: {config.PATREON_POST_URL}", flush=True)
            page.goto(config.PATREON_POST_URL, wait_until="domcontentloaded")

            menu_button = page.get_by_role("button", name="Menu for additional actions")
            try:
                menu_button.wait_for(state="visible", timeout=LINK_DETECT_TIMEOUT_MS)
                link_exists = True
            except PlaywrightTimeoutError:
                link_exists = False

            if link_exists:
                print("Existing link block found - deleting it first.", flush=True)
                menu_button.click()
                page.get_by_role("menuitem", name="Delete").click()
                page.get_by_role("button", name="Delete").click()
            else:
                print("No existing link block found - skipping delete, adding a new one.", flush=True)

            page.locator("button").filter(has_text="Link").click()
            url_box = page.get_by_role("textbox", name="Type or paste URL")
            url_box.click()
            url_box.fill(new_link)
            page.get_by_role("button", name="Update").click()

            # The Update button sometimes navigates, sometimes just settles
            # in place - a short fixed wait covers both cases reliably.
            page.wait_for_timeout(POST_UPDATE_SETTLE_MS)

            _dump_debug(page, screenshot_dir, "patreon_update_success")
            print("Patreon post updated with the new MEGA link.", flush=True)

        except Exception:
            _dump_debug(page, screenshot_dir, "patreon_update_failure")
            raise
        finally:
            context.close()
            browser.close()
