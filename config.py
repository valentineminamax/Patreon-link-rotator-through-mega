# ==========================================================
# Non-secret configuration
# Real credentials (MEGA login, Patreon session) live in
# GitHub Secrets, NOT here — see README.md.
# ==========================================================

# --- MEGA settings ---
# The remote path (in your MEGA account) of the FILE whose public share
# link gets rotated. Must be a single file, not a folder, for the current
# rotation logic (mega-cp/mega-mv on a file path). Find the exact path
# via `mega-ls`.
MEGA_SOURCE_PATH = "https://mega.nz/folder/1zwlQBAT#p0vvriYuhfpfzZkZ8bgd7w"

# --- Patreon settings ---
# The post you edit every rotation (the one containing the MEGA link).
PATREON_POST_URL = "https://www.patreon.com/MinaValentine/posts/mega-link-test-165354775"

# CSS/data-tag selectors Playwright uses to find and edit the link
# on the Patreon post editor page. Right-click -> Inspect on the
# real page to get the current values — Patreon changes these
# occasionally, so this is the main thing you'll need to update
# if the bot starts failing.
SELECTORS = {
    "edit_button": ".Stack-module__Qen2pq__justifyContentCenter > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(2) > div:nth-child(2) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(4) > button:nth-child(1) > div:nth-child(1) > div:nth-child(2) > div:nth-child(1)",   # opens the editor
    "body_textbox": "div[data-tag='post-content-editor']",  # field containing the link
    "save_button": "button[data-tag='post-save-button']",   # publishes the edit
}

# --- Scheduling ---
# Also update the cron expression in .github/workflows/rotate.yml to match.
ROTATE_EVERY_HOURS = 4
