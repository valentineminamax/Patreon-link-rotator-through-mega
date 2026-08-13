"""
Rotates the MEGA public link – with rollback.
The old folder's export is NOT disabled until Patreon update succeeds.
"""

import subprocess
import time
import re
from config import MEGA_BASE_DIR, FOLDER_PREFIX, FOLDER_FIXED_NAME

def _run(cmd, stdin_input=None):
    result = subprocess.run(cmd, capture_output=True, text=True, input=stdin_input, timeout=120)
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
    """
    Find the active folder:
    1. First, check if a folder with the fixed name exists – use that.
    2. If not, fall back to the prefix logic (for the first run).
    3. Ensure exactly one folder is found.
    """
    entries = _list_base_dir()

    # Check for fixed name first
    if FOLDER_FIXED_NAME in entries:
        return FOLDER_FIXED_NAME

    # Fallback: look for folders starting with FOLDER_PREFIX
    candidates = [e for e in entries if e.startswith(FOLDER_PREFIX)]

    if len(candidates) == 0:
        raise RuntimeError(
            f"No folder starting with '{FOLDER_PREFIX}' found under {MEGA_BASE_DIR}. "
            f"mega-ls returned: {entries}. Check FOLDER_PREFIX in config.py."
        )

    if len(candidates) > 1:
        raise RuntimeError(
            f"Found {len(candidates)} folders matching '{FOLDER_PREFIX}*' under "
            f"{MEGA_BASE_DIR}: {candidates}. This means a previous run's cleanup "
            "step failed and left an old folder behind -- manually check the MEGA app "
            "and delete the stale one(s) yourself before re-running."
        )

    return candidates[0]

def _extract_timestamp(name: str) -> int:
    """Extract timestamp from folder name like 'patreon-1785868385'."""
    match = re.search(rf'{re.escape(FOLDER_PREFIX)}(\d+)', name)
    if match:
        return int(match.group(1))
    return 0

def rotate_mega_link() -> tuple[str, str, str, str]:
    """
    Returns: (new_link, old_path, old_name, temp_path)
    - old_path: folder to delete if Patreon update succeeds
    - old_name: name of old folder (for logging)
    - temp_path: folder to delete if Patreon update fails (rollback)
    """
    active_name = _find_active_folder()
    old_path = f"{MEGA_BASE_DIR}/{active_name}"

    # Create a temporary name with timestamp
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

    # DO NOT disable old export yet – we keep it alive until Patreon succeeds
    print("Step 2/3: old export remains active (safe).", flush=True)

    return link, old_path, active_name, temp_path

if __name__ == "__main__":
    print(rotate_mega_link())
