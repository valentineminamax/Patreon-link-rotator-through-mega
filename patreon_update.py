"""
Updates Patreon post using the official Patreon API instead of Playwright.
Faster, more reliable, and bypasses Cloudflare entirely.
"""

import os
import json
import re
import requests
from config import PATREON_POST_URL

# Patreon API endpoints
PATREON_API_BASE = "https://www.patreon.com/api/oauth2/v2"

# Get credentials from environment (GitHub Secrets)
PATREON_ACCESS_TOKEN = os.getenv("PATREON_ACCESS_TOKEN")
PATREON_CLIENT_ID = os.getenv("PATREON_CLIENT_ID")
PATREON_CLIENT_SECRET = os.getenv("PATREON_CLIENT_SECRET")

# The post ID is extracted from the URL
# Example: https://www.patreon.com/SirenSins/posts/uncut-videos-156969230
# The post ID is the number at the end: 156969230
# But your URL uses a slug format, so we need to fetch it dynamically.


def _extract_post_id_from_url(url: str) -> str:
    """Extract numeric post ID from Patreon post URL."""
    # Try to find a numeric ID at the end of the URL
    match = re.search(r'(\d+)$', url)
    if match:
        return match.group(1)
    # If it's a slug format, we'll fetch the post by slug later
    return None


def _get_campaign_id() -> str:
    """Get the campaign ID from the Patreon API."""
    headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}
    response = requests.get(
        f"{PATREON_API_BASE}/campaigns",
        headers=headers,
        params={"fields[campaign]": "id,name"}
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("data"):
        raise RuntimeError("No campaign found for this access token.")
    return data["data"][0]["id"]


def _find_post_by_url(campaign_id: str, post_url: str) -> dict:
    """
    Find a post by its URL. Since the URL is the canonical one,
    we can fetch all posts and match the URL.
    """
    headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}
    posts = []
    cursor = None

    # Paginate through all posts
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
        response.raise_for_status()
        data = response.json()

        for post in data.get("data", []):
            # Check if this post's URL matches the one we want
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

    # If not found by exact URL, try by title or content match
    # Fallback: find the post that contains the MEGA link
    for post in posts:
        content = post.get("attributes", {}).get("content", "")
        if "mega.nz" in content:
            return post

    raise RuntimeError(f"Could not find post with URL: {post_url}")


def update_patreon_link(new_link: str) -> None:
    """
    Update the Patreon post content with the new MEGA link.
    """
    # Check if all required credentials are available
    if not PATREON_ACCESS_TOKEN:
        raise RuntimeError(
            "PATREON_ACCESS_TOKEN not set. Please add it to GitHub Secrets."
        )

    # Get campaign ID
    print("📡 Fetching campaign ID...", flush=True)
    campaign_id = _get_campaign_id()
    print(f"   Campaign ID: {campaign_id}", flush=True)

    # Find the post by its URL
    print(f"📡 Finding post: {PATREON_POST_URL}", flush=True)
    post = _find_post_by_url(campaign_id, PATREON_POST_URL)
    post_id = post.get("id")
    post_attributes = post.get("attributes", {})
    current_content = post_attributes.get("content", "")
    title = post_attributes.get("title", "Untitled")

    print(f"   Found post: {title} (ID: {post_id})", flush=True)

    # Replace the old MEGA link with the new one in the content
    # Look for any MEGA link pattern and replace it
    old_link_pattern = r'(https?://mega\.nz/folder/[^#]+#[^\s"\'<>]+)'
    new_content = re.sub(old_link_pattern, new_link, current_content)

    if new_content == current_content:
        # If no MEGA link found, just append it (or warn)
        print("   ⚠️ No existing MEGA link found in content. Appending...", flush=True)
        new_content = current_content + f"\n\nMEGA Link: {new_link}"

    # Prepare the update payload
    update_data = {
        "data": {
            "type": "post",
            "id": post_id,
            "attributes": {
                "content": new_content,
            },
        }
    }

    # Send the update request
    print("📡 Updating post content...", flush=True)
    headers = {
        "Authorization": f"Bearer {PATREON_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    response = requests.patch(
        f"{PATREON_API_BASE}/posts/{post_id}",
        headers=headers,
        json=update_data,
    )

    if response.status_code != 200:
        error_detail = response.json() if response.text else "No details"
        raise RuntimeError(
            f"Failed to update post (HTTP {response.status_code}): {error_detail}"
        )

    print("✅ Patreon post updated successfully!", flush=True)
    print(f"   New link: {new_link}", flush=True)


# For testing locally
if __name__ == "__main__":
    # Test with a dummy link
    test_link = "https://mega.nz/folder/test#test"
    update_patreon_link(test_link)
