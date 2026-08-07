import requests

PATREON_CLIENT_ID = os.getenv("PATREON_CLIENT_ID")
PATREON_CLIENT_SECRET = os.getenv("PATREON_CLIENT_SECRET")
PATREON_REFRESH_TOKEN = os.getenv("PATREON_REFRESH_TOKEN")  # Add this secret


def refresh_access_token() -> str:
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
    print(f"   New access token: {new_access_token[:20]}...", flush=True)
    
    # Store the new refresh token for next time
    if new_refresh_token:
        print(f"   New refresh token: {new_refresh_token[:20]}...", flush=True)
        print("   ⚠️ Update your PATREON_REFRESH_TOKEN secret with this new value!", flush=True)
    
    return new_access_token


def _get_campaign_id() -> str:
    """Get the campaign ID from the Patreon API."""
    # Check if token is valid, refresh if needed
    headers = {"Authorization": f"Bearer {PATREON_ACCESS_TOKEN}"}
    response = requests.get(
        f"{PATREON_API_BASE}/campaigns",
        headers=headers,
        params={"fields[campaign]": "id,name"}
    )
    
    # If 401, refresh token and retry
    if response.status_code == 401:
        print("⚠️ Access token expired. Refreshing...", flush=True)
        new_token = refresh_access_token()
        # Update the global token for this run
        global PATREON_ACCESS_TOKEN
        PATREON_ACCESS_TOKEN = new_token
        # Retry the request with new token
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
