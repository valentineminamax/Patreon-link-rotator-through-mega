"""
Central config. Everything here is read from environment variables so that
GitHub Actions secrets can be passed straight through with no code changes.

Required secrets (Settings -> Secrets and variables -> Actions -> Secrets):
    MEGA_EMAIL              - your MEGA account email
    MEGA_PASSWORD           - your MEGA account password
    PATREON_SESSION_JSON    - Playwright storage_state JSON for a logged-in
                               Patreon session (see README section below)

Optional secrets (proxy - only used for the Patreon browser session):
    PROXY_HOST
    PROXY_PORT
    PROXY_USERNAME
    PROXY_PASSWORD

Plain vars (Settings -> Secrets and variables -> Actions -> Variables -
not secret, but still required unless you're fine with the fallback):
    PATREON_POST_URL   - required. Full /edit URL of the Patreon post that
                          holds the MEGA link block. No fallback - the run
                          fails fast if this isn't set.
    MEGA_BASE_DIR      - default: /Root/mega-link-rotation
    FOLDER_PREFIX      - default: share_
    FOLDER_FIXED_NAME  - default: "" (disabled). If set, the active folder
                          is always renamed to this after a successful
                          rotation, so the next run finds it by fixed name
                          instead of by newest timestamp.
"""

import os


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable/secret: {name}. "
            "Check your GitHub Actions secrets configuration."
        )
    return value


def _get(name: str, default: str) -> str:
    """Like os.environ.get, but treats an empty string as unset too - GitHub
    Actions sets ${{ vars.X }} to '' rather than omitting it when a repo
    Variable isn't configured, which would otherwise silently skip the
    default below."""
    value = os.environ.get(name, "")
    return value if value else default


# --- MEGA -------------------------------------------------------------
MEGA_BASE_DIR = _get("MEGA_BASE_DIR", "/Root/mega-link-rotation")
FOLDER_PREFIX = _get("FOLDER_PREFIX", "share_")
FOLDER_FIXED_NAME = _get("FOLDER_FIXED_NAME", "")

MEGA_EMAIL = _require("MEGA_EMAIL")
MEGA_PASSWORD = _require("MEGA_PASSWORD")

# --- Patreon ------------------------------------------------------------
PATREON_SESSION_JSON = _require("PATREON_SESSION_JSON")
PATREON_POST_URL = _require("PATREON_POST_URL")

# --- Proxy (Patreon browser session only) --------------------------------
PROXY_HOST = os.environ.get("PROXY_HOST")
PROXY_PORT = os.environ.get("PROXY_PORT")
PROXY_USERNAME = os.environ.get("PROXY_USERNAME")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD")
