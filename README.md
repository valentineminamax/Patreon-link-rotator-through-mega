# Patreon MEGA Link Rotator

Automatically rotates your MEGA share link every few hours and updates
the Patreon post that contains it, so leaked links go stale fast.

## Setup

### 1. Fill in `config.py`
- `MEGA_SOURCE_PATH` — the exact file path in your MEGA account to rotate
  (must be a file, not a folder — check with `mega-ls`).
- `PATREON_POST_URL` — the post you always edit.
- `SELECTORS` — see the next section. Don't guess these by hand; use
  codegen (below), it's much easier and far more reliable.

### 1b. Get real selectors with Playwright codegen (do this, don't hand-inspect)
Instead of manually right-clicking elements in dev tools, let Playwright
record your clicks and generate the exact selectors for you:

```bash
pip install playwright
playwright install chromium
playwright codegen https://www.patreon.com
```

This opens two windows: a browser, and an "Inspector" panel showing
generated code live.
1. In the browser, log into Patreon normally and go to your post.
2. Click the button that opens the editor. Watch the Inspector panel —
   a line like `page.click("button:has-text('Edit post')")` appears.
   That's your `edit_button` selector.
3. Click into the text field containing the link. The Inspector shows
   the selector for that field → that's your `body_textbox`.
4. Click the save/publish button → that's your `save_button`.
5. Copy each generated selector string into `SELECTORS` in `config.py`.
6. Close both windows when done (don't actually need to finish editing
   the real post — you can cancel/undo after you've captured the clicks).

If Patreon ever redesigns the editor and the bot starts failing, redo
this step to get fresh selectors.

### 2. Capture a Patreon session (one time, on your own computer)
```bash
pip install playwright
playwright install chromium
python login_once.py
```
Log in (with 2FA if you have it) in the browser window that opens,
then hit Enter in the terminal. This creates `patreon_session.json`.

### 3. Create a **private** GitHub repo and push this code
Keep it private — secrets and automation like this shouldn't be public.

### 4. Add repo secrets (Settings → Secrets and variables → Actions)
- `MEGA_EMAIL` — your MEGA login email
- `MEGA_PASSWORD` — your MEGA password
- `PATREON_SESSION_JSON` — paste the full contents of `patreon_session.json`

### 5. Done
The workflow in `.github/workflows/rotate.yml` runs every 4 hours
automatically (edit the cron line to change frequency), and you can
also trigger it manually from the Actions tab any time.

## Important things to know

- **Session expiry**: Patreon sessions eventually expire. If the bot
  starts failing, you'll likely need to re-run `login_once.py` and
  update the `PATREON_SESSION_JSON` secret.
- **Selector drift**: if Patreon redesigns their post editor, the
  `SELECTORS` in `config.py` will need updating.
- **This is browser automation of your own account** — it's your
  content and your login, but automating actions on Patreon's site
  isn't something they officially support, so keep an eye on their
  terms of service for account automation and use your own judgment
  on frequency.
- **This slows pirates down, it doesn't stop them** — anyone
  resharing fast after each rotation will still get through. Two
  things that help more with the *root* problem:
  - **Per-patron/unique links** (if MEGA/your platform supports it)
    so a leak can be traced back to who shared it.
  - Lower-tier/watermarked previews so leaks are less damaging even
    when they happen.

## Cost
GitHub Actions is free for private repos up to 2,000 minutes/month.
A rotation run (MEGA login + Playwright + update) typically takes a
few minutes, so running every 4 hours (~180 runs/month) comfortably
fits inside the free tier.
