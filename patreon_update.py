"""
Updates Patreon post using the official Patreon API.
Includes automatic token refresh and proper URL encoding.
"""

import os
import re
import requests
from urllib.parse import urlencode
from config import PATREON_POST_URL

PATREON_API_BASE = "https://www.patreon.com/api/oauth2/v2"

# Read from environment (GitHub Secrets)
PATREON_ACCESS_TOKEN = os.getenv("PATREON_ACCESS_TOKEN")
PATREON_REFRESH_TOKEN = os.getenv("PATREON_REFRESH_TOKEN")
PATREON_CLIENT_ID = os.getenv("PATREON_CLIENT_ID")
PATREON_CLIENT_SECRET = os.getenv("PATREON_CLIENT_SECRET")


def _refresh_access_token() -> str:
    """Refresh the creator access token using the refresh token."""
    url = "https://www.patreon.com/api/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": PATREON_REFRESH_TOKEN,
        "client_id": PATREON_CLIENT_ID,
        "client_secret": PATREON_CLIENT_SECRET,
    }
    response = requests.post(url, data=data)
    response.raise_for_status()
    tokens = response.json()
    new_access_token = tokens.get("access_token")
    new_refresh_token = tokens.get("refresh_token")

    print("✅ Access token refreshed successfully!", flush=True)
    if new_refresh_token:
        print(f"   ⚠️ Update your PATREON_REFRESH_TOKEN secret with: {new_refresh_token}", flush=True)

    return new_access_token


def _get_campaign_id() -> str:
    """
    Get the campaign ID from the Patreon API.
    Uses URL-encoded field parameter to avoid 400 errors.
    """
    headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}

    # Properly URL-encode the fields parameter
    params = {"fields[campaign]": "id,creation_name"}

    response = requests.get(
        f"{PATREON_API_BASE}/campaigns",
        headers=headers,
        params=params,
    )

    # If token expired (401), refresh and retry
    if response.status_code == 401:
        print("⚠️ Access token expired. Refreshing...", flush=True)
        new_token = _refresh_access_token()
        # Update the global token for this run
        global PATREON_ACCESS_TOKEN
        PATREON_ACCESS_TOKEN = new_token
        headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}
        response = requests.get(
            f"{PATREON_API_BASE}/campaigns",
            headers=headers,
            params=params,
        )

    response.raise_for_status()
    data = response.json()

    if not data.get("data"):
        raise RuntimeError("No campaign found for this access token.")

    return data["data"][0]["id"]


def _find_post_by_url(campaign_id: str, post_url: str) -> dict:
    """
    Find a post by its URL. Fetches all posts and matches the URL.
    """
    headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}
    posts = []
    cursor = None

    while True:
        params = {
            "fields[post]": "id,title,content,url,published_at",
            "page[limit]": 25,
        }
        if cursor:
            params["page[cursor]"] = cursor

        response = requests.get(
            f"{PATREON_API_BASE}/campaigns/{campaign_id}/posts",
            headers=headers,
            params=params,
        )

        # Handle token expiry during pagination
        if response.status_code == 401:
            print("⚠️ Access token expired during pagination. Refreshing...", flush=True)
            new_token = _refresh_access_token()
            global PATREON_ACCESS_TOKEN
            PATREON_ACCESS_TOKEN = new_token
            headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}
            continue

        response.raise_for_status()
        data = response.json()

        for post in data.get("data", []):
            post_url_from_api = post.get("attributes", {}).get("url")
            if post_url_from_api and post_url_from_api == post_url:
                return post
            posts.append(post)

        # Check for next page
        links = data.get("links", {})
        if "next" in links:
            cursor = links["next"].split("page%5Bcursor%5D=")[-1]
        else:
            break

    # Fallback: find post containing "mega.nz"
    for post in posts:
        content = post.get("attributes", {}).get("content", "")
        if "mega.nz" in content:
            return post

    raise RuntimeError(f"Could not find post with URL: {post_url}")


def update_patreon_link(new_link: str) -> None:
    """
    Update the Patreon post content with the new MEGA link.
    """
    if not PATREON_ACCESS_TOKEN:
        raise RuntimeError("PATREON_ACCESS_TOKEN not set. Please add it to GitHub Secrets.")

    print("📡 Fetching campaign ID...", flush=True)
    campaign_id = _get_campaign_id()
    print(f"   Campaign ID: {campaign_id}", flush=True)

    print(f"📡 Finding post: {PATREON_POST_URL}", flush=True)
    post = _find_post_by_url(campaign_id, PATREON_POST_URL)
    post_id = post.get("id")
    post_attributes = post.get("attributes", {})
    current_content = post_attributes.get("content", "")
    title = post_attributes.get("title", "Untitled")

    print(f"   Found post: {title} (ID: {post_id})", flush=True)

    # Replace the old MEGA link with the new one
    old_link_pattern = r'(https?://mega\.nz/folder/[^#]+#[^\s"\'<>]+)'
    new_content = re.sub(old_link_pattern, new_link, current_content)

    if new_content == current_content:
        print("   ⚠️ No existing MEGA link found in content. Appending...", flush=True)
        new_content = current_content + f"\n\nMEGA Link: {new_link}"

    update_data = {
        "data": {
            "type": "post",
            "id": post_id,
            "attributes": {
                "content": new_content,
            },
        }
    }

    print("📡 Updating post content...", flush=True)
    headers = {
        "Authorization": f"Bearer {PATREON_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "MEGA-Link-Rotator/1.0",  # Helps avoid 400 errors[reference:1]
    }

    response = requests.patch(
        f"{PATREON_API_BASE}/posts/{post_id}",
        headers=headers,
        json=update_data,
    )

    # Handle token expiry on the PATCH request
    if response.status_code == 401:
        print("⚠️ Access token expired. Refreshing and retrying...", flush=True)
        new_token = _refresh_access_token()
        global PATREON_ACCESS_TOKEN
        PATREON_ACCESS_TOKEN = new_token
        headers["Authorization"] = f"Bearer {PATREON_ACCESS_TOKEN}"
        response = requests.patch(
            f"{PATREON_API_BASE}/posts/{post_id}",
            headers=headers,
            json=update_data,
        )

    if response.status_code != 200:
        error_detail = response.text if response.text else "No details"
        raise RuntimeError(f"Failed to update post (HTTP {response.status_code}): {error_detail}")

    print("✅ Patreon post updated successfully!", flush=True)
    print(f"   New link: {new_link}", flush=True)


# For local testing
if __name__ == "__main__":
    test_link = "https://mega.nz/folder/test#test"
    update_patreon_link(test_link)
