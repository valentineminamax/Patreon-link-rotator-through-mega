"""
Orchestrates one full rotation:

  1. mega-login
  2. mega_rotate.rotate_mega_link()      - copy active folder, export new
                                            link, old export left untouched
  3. patreon_update.update_patreon_link() - swap the link on the live post
       - on failure: mega_rotate.rollback_rotation()  -> exit non-zero
       - on success: mega_rotate.complete_rotation()  -> old folder retired
  4. mega-logout (always, via finally)

Exits non-zero on any unrecoverable failure so the GitHub Actions job is
marked failed and the artifact-upload step fires.
"""

import os
import subprocess
import sys
import traceback

import config
import mega_rotate
import patreon_update

SCREENSHOT_DIR = "artifacts"


def _mega_login() -> None:
    print("Logging into MEGA...", flush=True)
    result = subprocess.run(
        ["mega-login", config.MEGA_EMAIL, config.MEGA_PASSWORD],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        # Don't echo stdout/stderr verbatim here - MEGAcmd sometimes echoes
        # the invoked command line, which would leak the password into logs.
        raise RuntimeError(f"mega-login failed (exit {result.returncode}).")
    print("MEGA login OK.", flush=True)


def _mega_logout() -> None:
    subprocess.run(["mega-logout"], capture_output=True, text=True, timeout=30)


def main() -> None:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    _mega_login()
    try:
        new_link, old_path, temp_path = mega_rotate.rotate_mega_link()
        print(f"New MEGA link ready: {new_link}", flush=True)
        print(f"  old (still exported): {old_path}", flush=True)
        print(f"  temp (new export):    {temp_path}", flush=True)

        try:
            patreon_update.update_patreon_link(new_link, screenshot_dir=SCREENSHOT_DIR)
        except Exception:
            print(
                "Patreon update failed - rolling back MEGA rotation "
                "(old folder's export stays live, temp folder removed).",
                flush=True,
            )
            traceback.print_exc()
            mega_rotate.rollback_rotation(temp_path)
            sys.exit(1)

        final_path = mega_rotate.complete_rotation(old_path, temp_path)
        print(f"Rotation complete. Active folder: {final_path}", flush=True)

    finally:
        _mega_logout()


if __name__ == "__main__":
    main()
