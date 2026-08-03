# ==========================================================
# Non-secret configuration
# Real credentials (MEGA login, Patreon session) live in
# GitHub Secrets, NOT here — see README.md.
# ==========================================================

# --- MEGA settings ---
# Your link lives on the video folder itself, inside a fixed outer
# folder (e.g. "Patreon Content"). Every rotation copies that folder
# to a brand new, timestamped name (e.g. "patreon-1754160000") so the
# link is guaranteed to change, then deletes the old one entirely.
#
# There should only ever be ONE folder starting with FOLDER_PREFIX
# under MEGA_BASE_DIR at a time. If mega_rotate.py ever finds more
# than one, it stops and refuses to guess -- that means a previous
# run's cleanup failed and needs a human to sort out manually before
# rotating again (this happened once already from an old version of
# this script that guessed instead of stopping -- don't reintroduce
# that).
#
# No manual setup needed before the first run — your existing
# "patreon-1" folder already matches this prefix and is used as-is.
MEGA_BASE_DIR = "/Patreon Content"
FOLDER_PREFIX = "patreon-"

# --- Patreon settings ---
# The post you edit every rotation (the one containing the MEGA link).
PATREON_POST_URL = "https://www.patreon.com/MinaValentine/posts/mega-link-test-165354775?utm_medium=clipboard_copy&utm_source=copyLink&utm_campaign=postshare_creator&utm_content=join_link"

# CSS selectors Playwright uses to drive Patreon's post editor.
# Captured via browser devtools "Copy selector" on the real page.
#
# WARNING: the Button-module__XXXXXX__ classnames contain a hash that
# Patreon regenerates on redeploys. This is the single most likely
# thing to break. When it does, re-capture with:
#   playwright codegen https://www.patreon.com
SELECTORS = {
    # Opens the post editor
    "edit_button": ".Stack-module__Qen2pq__justifyContentCenter > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(2) > div:nth-child(2) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > div:nth-child(4) > button:nth-child(1) > div:nth-child(1) > div:nth-child(2) > div:nth-child(1)",

    # The 3-dot menu on the existing link content block
    "block_menu_button": "#_r_i7_ > div:nth-child(1) > span:nth-child(1) > svg:nth-child(1)",

    # "Delete" option inside that menu
    # NOTE: this class is a generic text-align utility — if it matches
    # more than one menu item, this is the first thing to fix.
    "delete_option": ".TextLayoutBundle-module__IsQm8a__alignLeft",

    # Confirm button in the "are you sure you want to delete" popup
    "confirm_delete_button": ".Button-module__YRbc6a__themeCritical > div:nth-child(1) > div:nth-child(1)",

    # "Link" option in the add-content picker that appears after delete
    "link_option_button": "button.Button-module__YRbc6a__root:nth-child(4) > div:nth-child(1)",

    # The field you type the new link into
    "link_input_field": "#_r_g6_",

    # Final button that saves/publishes the change
    "update_button": "button.Button-module__YRbc6a__root:nth-child(5) > div:nth-child(1) > div:nth-child(1) > div:nth-child(1)",
}

# --- Scheduling ---
# Also update the cron expression in .github/workflows/rotate.yml to match.
ROTATE_EVERY_HOURS = 4
