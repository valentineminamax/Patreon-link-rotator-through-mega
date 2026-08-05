# Patreon MEGA Link Rotator

Automatically rotates your MEGA share link every few hours and updates the Patreon post that contains it, so leaked links go stale fast.

## Features

- **MEGA rotation** – Copies your video folder to a new timestamped name, generates a fresh share link, and deletes the old folder only after Patreon is updated.
- **Patreon update** – Automatically edits your Patreon post to replace the old MEGA link with the new one.
- **Cloudflare bypass** – Uses a residential proxy (DataImpulse) and Patchright (stealth browser) to avoid Cloudflare detection.
- **Failure‑safe** – If the Patreon update fails, the new folder is deleted automatically and the old link stays active. No dead links for your subscribers.
- **Self‑healing** – If the bot crashes mid‑operation, the next run automatically detects and cleans up the mess, then continues normally.
- **Runs on GitHub Actions** – Free scheduled execution every 4 hours (or any cron schedule you choose).

## Setup

### 1. Fill in `config.py`

```python
# MEGA settings
MEGA_BASE_DIR = "/Patreon Content"
FOLDER_PREFIX = "patreon-"

# Patreon settings
PATREON_POST_URL = "https://www.patreon.com/posts/YOUR_POST_ID_HERE"

# Scheduling (keep in sync with cron expression)
ROTATE_EVERY_HOURS = 4
```

**Important:** The `PATREON_POST_URL` must point to the exact post that contains your MEGA link. The post ID is the number at the end of the URL, e.g., `https://www.patreon.com/posts/12345678` → `12345678`.

### 2. Install Python dependencies

```
pip install playwright patchright playwright-captcha
patchright install --with-deps chromium
```

### 3. Capture your Patreon session (one time, on your computer)

```
python login_once.py
```

This opens a browser window. Log in to Patreon (with 2FA if enabled), then press Enter in the terminal. This creates `patreon_session.json`.

### 4. Set up GitHub Secrets

Go to your repository's **Settings → Secrets and variables → Actions** and add these secrets:

| Secret name ↕▾ | Value ↕▾ |
|---|---|
| −`MEGA_EMAIL` | Your MEGA login email |
| −`MEGA_PASSWORD` | Your MEGA password |
| −`PATREON_SESSION_JSON` | The **full contents** of `patreon_session.json` |
| −`PROXY_HOST` | Your proxy host (e.g., `gw.dataimpulse.com`) |
| −`PROXY_PORT` | Your proxy port (e.g., `823`) |
| −`PROXY_USERNAME` | Your proxy username |
| −`PROXY_PASSWORD` | Your proxy password |
⚙

**Note:** A residential proxy (like DataImpulse) is required to bypass Cloudflare on GitHub Actions. Without it, the bot will be blocked.

### 5. Push the code to a private GitHub repository

Keep the repository private – secrets and automation shouldn't be public.

### 6. Done!

The workflow in `.github/workflows/rotate.yml` runs automatically on the schedule you set (every 4 hours by default). You can also trigger it manually from the Actions tab.

## How it works

### MEGA rotation flow (failure‑safe)

1. **Check for previous crash** – If there are multiple `patreon-*` folders, auto‑detect and clean up.
2. **Copy** the active folder to a new timestamped name.
3. **Disable** the old folder's public export.
4. **Generate** a new export link for the new folder.
5. **Update Patreon** – Replace the old link in the post with the new one.
6. **If success:** Delete the old folder.
7. **If failure:** Delete the new folder (rollback) – the old link stays active.

### Patreon update flow

1. Navigate to the post.
2. Click the "Edit" button.
3. Open the 3‑dot menu on the existing link block.
4. Click "Delete" and confirm.
5. Choose "Link" from the add‑content picker.
6. Type the new MEGA link into the field.
7. Click "Update" to save.

### Why a residential proxy is needed

GitHub Actions runners use datacenter IPs that are often flagged by Cloudflare. The proxy gives you a residential IP, which is trusted, allowing the bot to bypass the Cloudflare challenge.

## Troubleshooting

### Cloudflare challenge persists

- Verify your proxy secrets are correctly set.
- Check your proxy balance/usage on the DataImpulse dashboard.
- Try a different proxy provider if needed.

### Patreon selectors break

If Patreon redesigns the post editor, the role‑based selectors (`get_by_role`, `locator().filter()`) may need updating. Use `playwright codegen https://www.patreon.com/posts/YOUR_POST_ID` to capture new selectors.

### MEGA folder count error

If the bot reports multiple `patreon-*` folders, it means a previous run crashed during cleanup. The bot now auto‑recovers, but if it still fails, manually log in to MEGA and delete the extra folder(s).

## Need help?

If you run into issues that this guide doesn't cover, feel free to reach out:

- 📧 **Email:** ashx0147@gmail.com
- 💬 **Discord:** @alone_shonen

I'll do my best to help you get the bot running smoothly. 

## Important Notes

- **Session expiry** – Patreon sessions eventually expire. If the bot starts failing, re‑run `login_once.py` and update the `PATREON_SESSION_JSON` secret.
- **Proxy cost** – Each run uses only a few MB of data – fractions of a cent per run with most residential proxy services.
- **Patreon ToS** – Automating actions on Patreon may violate their Terms of Service. Use at your own risk.

## Cost

- GitHub Actions: Free for private repositories (up to 2,000 minutes/month).
- Residential proxy: ~$0.01–$0.05 per run (depending on provider and data usage).

## Files

| File ↕▾ | Purpose ↕▾ |
|---|---|
| −`config.py` | Non‑secret configuration |
| −`mega_rotate.py` | MEGA folder rotation (failure‑safe) |
| −`patreon_update.py` | Patreon post update using Patchright |
| −`main.py` | Orchestrates rotation + update |
| −`login_once.py` | One‑time Patreon session capture |
| −`.github/workflows/rotate.yml` | GitHub Actions workflow |
⚙

## License

This project is for educational and research purposes only. Use responsibly and respect the Terms of Service of all platforms involved.

