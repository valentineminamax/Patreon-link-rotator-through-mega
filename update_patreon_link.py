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


class VerificationInconclusiveError(Exception):
    """
    Raised when the script could not confirm whether the edit succeeded or
    not (e.g. the verification page never finished loading). This is
    deliberately a different exception type from a confirmed content
    mismatch, because the two require different responses: a confirmed
    mismatch means the edit genuinely didn't take and it's safe to roll
    back to the old link. An inconclusive result means we simply don't
    know - the 2026-08-13 network trace showed the save PATCH succeeding
    server-side almost immediately, so treating "couldn't load the
    verification page" the same as "verified the edit failed" was
    destroying good temp folders on pure navigation/proxy hiccups that had
    nothing to do with whether the edit worked.
    """
    pass


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


async def _goto_with_retries(
    page: Page,
    url: str,
    attempts: int = 3,
    per_attempt_timeout_ms: int = 25000,
) -> None:
    """
    Navigate with several short attempts instead of one long one.

    FIX (2026-08-13): the previous code did a single
    `page.goto(url, wait_until="domcontentloaded")` with the full 90s
    NAV_TIMEOUT_MS. On the run that prompted this fix, that call hung for
    the entire 90s and raised a bare TimeoutError - no title/Cloudflare
    check ever ran (that code only executes *after* goto returns), and no
    debug artifacts were written for that path, because the failure never
    reached any of that logic. A single long wait gives no chance to
    recover from a transient stall (proxy hiccup, a Cloudflare interstitial
    that keeps re-triggering before "domcontentloaded" fires, brief
    rate-limiting right after an edit, etc.) - it just burns the whole
    budget once and gives up.

    This retries a few shorter navigations instead, using wait_until=
    "commit" (returns as soon as the response starts arriving, rather than
    waiting for the full DOM) so a stuck attempt can be abandoned and
    retried quickly, then explicitly waits for "domcontentloaded" with its
    own short timeout. If ALL attempts fail to even get a response, this
    raises VerificationInconclusiveError - NOT a generic exception - so
    the caller can tell "we don't know what happened" apart from "we
    loaded the page and the link isn't there."
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until="commit", timeout=per_attempt_timeout_ms)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=per_attempt_timeout_ms)
            except Exception as e:
                # Got a response (commit succeeded) but DOM never settled -
                # still worth trying a Cloudflare check before giving up,
                # since the interstitial itself counts as "committed."
                log.warning("Attempt %d/%d: domcontentloaded did not settle: %s", attempt, attempts, e)
            await _handle_cloudflare(page)
            return
        except Exception as e:
            last_err = e
            log.warning("Attempt %d/%d: navigation to %s did not complete: %s", attempt, attempts, url, e)
            await asyncio.sleep(2 * attempt)  # small backoff between attempts

    raise VerificationInconclusiveError(
        f"Could not load {url} after {attempts} attempts - last error: {last_err}"
    )


async def _wait_for_editor_ready(
    page: Page,
    edit_url: str,
    attempts: int = 2,
    ready_timeout_ms: int = 30000,
) -> None:
    """
    Wait for the post editor to actually finish hydrating before touching
    it, instead of trusting a fixed sleep(2).

    FIX (2026-08-13): the previous code did `goto(edit_url,
    wait_until="domcontentloaded")` + `sleep(2)` + a Cloudflare title check,
    then went straight for the "Link" toolbar button, relying on Playwright's
    default 60s action-timeout as the only backstop. A captured failure
    screenshot showed the page still full of grey shimmer/skeleton
    placeholders 60+ seconds after that goto - the document had loaded
    (title was plain "Patreon", no Cloudflare interstitial, ~387KB of real
    payload) but Patreon's client-side app simply hadn't rendered the real
    editor yet. The click didn't fail because the selector was wrong; there
    was nothing there yet to click.

    This waits explicitly for the Link toolbar button to become visible
    (the actual signal we care about, not a guess at how long hydration
    takes). If it's not visible within ready_timeout_ms, it reloads the
    page once and tries again - a fresh load can clear a stuck client-side
    hydration - rather than sitting on a page that may never finish.
    Raises the underlying TimeoutError if it still isn't ready after all
    attempts, same as before, so existing failure-artifact handling in the
    caller still fires.
    """
    link_button = page.locator(CONFIG["LOCATOR_LINK_BUTTON_SELECTOR"])
    for attempt in range(1, attempts + 1):
        try:
            await link_button.wait_for(state="visible", timeout=ready_timeout_ms)
            log.info("Editor ready (attempt %d/%d).", attempt, attempts)
            return
        except Exception as e:
            log.warning(
                "Attempt %d/%d: editor toolbar not ready after %dms (%s)",
                attempt, attempts, ready_timeout_ms, e,
            )
            if attempt == attempts:
                # Out of attempts - let this real TimeoutError (with its
                # own call-log context) propagate to the caller, same as
                # before this fix existed.
                raise
            log.info("Reloading editor and retrying...")
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await _handle_cloudflare(page)


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

            # FIX: previously went straight into _replace_link_in_editor()
            # after just a fixed sleep(2) + Cloudflare title check, relying
            # on Playwright's default 60s action-timeout as the only
            # backstop against a slow-hydrating editor. A captured failure
            # showed the page still full of skeleton placeholders 60+
            # seconds in - the toolbar button genuinely wasn't there yet,
            # not a selector problem. This waits for a real readiness
            # signal and reloads once if it stalls, instead of gambling on
            # a fixed sleep being long enough.
            await _wait_for_editor_ready(page, edit_url)

            await _replace_link_in_editor(page, new_link)

            log.info("Verifying public post: %s", public_url)
            # FIX: previously a single page.goto(..., wait_until=
            # "domcontentloaded") using the full 90s NAV_TIMEOUT_MS. On the
            # run that prompted this fix, that call hung for the entire 90s
            # and threw a bare TimeoutError before the Cloudflare check (or
            # any content check) ever ran. _goto_with_retries() replaces
            # this with several shorter attempts and raises
            # VerificationInconclusiveError specifically if none of them
            # even produce a page - see below for why that distinction
            # matters.
            await _goto_with_retries(page, public_url)
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
    except VerificationInconclusiveError:
        # FIX: this used to fall into the same bare `except Exception` as a
        # confirmed content mismatch, which rolled back (deleted) the temp
        # folder - i.e. threw away a link that, per the 2026-08-13 network
        # trace, had almost certainly already saved successfully server-side.
        # An inconclusive verification means we genuinely don't know whether
        # the edit took, so the safe move is to do nothing destructive:
        # leave the old export + old folder alone (still live, so the post
        # is never link-less) and leave the new temp folder alone too (so
        # nothing is lost if the edit did succeed). This just surfaces
        # loudly and exits non-zero so it's visible in the Actions run,
        # for manual confirmation - the live post should be checked by hand
        # before the next scheduled run.
        log.exception(
            "Could not verify the public post (navigation never completed) - "
            "NOT rolling back and NOT cleaning up. Old link is still live at "
            "%s, new folder %s was left in place in case the edit actually "
            "succeeded. Check the live post manually.",
            old_path, temp_path,
        )
        sys.exit(2)
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
