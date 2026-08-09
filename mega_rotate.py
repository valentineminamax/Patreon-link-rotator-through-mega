"""
Rotates the MEGA public link – without disabling the old export.
The old folder's export is left active until Patreon update succeeds.
"""

import subprocess
import time
import re
from config import MEGA_BASE_DIR, FOLDER_PREFIX, FOLDER_FIXED_NAME

MEGA_TIMEOUT = 60  # reasonable timeout for copy/export

def _run(cmd, stdin_input=None, timeout=MEGA_TIMEOUT):
    result = subprocess.run(cmd, capture_output=True, text=True, input=stdin_input, timeout=timeout)
    if result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", flush=True)
        print(f"stdout: {result.stdout}", flush=True)
        print(f"stderr: {result.stderr}", flush=True)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result

def _list_base_dir() -> list[str]:
    result = subprocess.run(
        ["mega-ls", MEGA_BASE_DIR], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"mega-ls failed (exit {result.returncode}) — this usually means "
            "you're not logged in (`mega-login`) or MEGAcmd isn't on PATH yet, "
            f"not that the folder is missing.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return [line.strip().rstrip("/") for line in result.stdout.splitlines() if line.strip()]

def _find_active_folder() -> str:
    entries = _list_base_dir()

    if FOLDER_FIXED_NAME in entries:
        return FOLDER_FIXED_NAME

    candidates = [e for e in entries if e.startswith(FOLDER_PREFIX)]

    if len(candidates) == 0:
        raise RuntimeError(
            f"No folder starting with '{FOLDER_PREFIX}' found under {MEGA_BASE_DIR}. "
            f"mega-ls returned: {entries}. Check FOLDER_PREFIX in config.py."
        )

    if len(candidates) > 1:
        # Sort by timestamp (newest first) and pick the newest
        def extract_ts(name):
            match = re.search(rf'{re.escape(FOLDER_PREFIX)}(\d+)', name)
            return int(match.group(1)) if match else 0
        candidates.sort(key=extract_ts, reverse=True)
        print(f"⚠️ Found {len(candidates)} folders matching '{FOLDER_PREFIX}*'. "
              f"Using the newest: {candidates[0]}. "
              f"The others will be cleaned up later after a successful update.", flush=True)
        # Do NOT delete the older ones here – deletion will happen after Patreon succeeds.
    return candidates[0]

def rotate_mega_link() -> tuple[str, str, str, str]:
    """
    Returns: (new_link, old_path, old_name, temp_path)
    - old_path: folder to delete if Patreon update succeeds
    - old_name: name of old folder (for logging)
    - temp_path: folder to delete if Patreon update fails (rollback)
    """
    active_name = _find_active_folder()
    old_path = f"{MEGA_BASE_DIR}/{active_name}"

    timestamp = int(time.time())
    temp_name = f"{FOLDER_PREFIX}{timestamp}"
    temp_path = f"{MEGA_BASE_DIR}/{temp_name}"

    print(f"Step 1/3: copying {old_path} -> {temp_path} ...", flush=True)
    _run(["mega-cp", old_path, temp_path])
    print("Step 1/3: copy done.", flush=True)

    print("Step 2/3: generating new export link on temp folder ...", flush=True)
    result = _run(["mega-export", "-a", temp_path], stdin_input="yes\nyes\nyes\n")
    for line in result.stdout.splitlines():
        if "https://mega.nz" in line:
            link = line.strip().split()[-1]
            print(f"Step 2/3: new link generated: {link}", flush=True)
            break
    else:
        raise RuntimeError("Could not parse new MEGA link from mega-export output:\n" + result.stdout)

    # DO NOT disable old export – we keep it alive until Patreon succeeds
    print("Step 3/3: old export remains active (safe).", flush=True)

    return link, old_path, active_name, temp_path

if __name__ == "__main__":
    print(rotate_mega_link())
