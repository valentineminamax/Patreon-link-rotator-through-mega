"""
Rotates the MEGA public link – with rollback and auto‑cleanup of stale folders.
The old folder's export is NOT disabled until Patreon update succeeds.
If multiple folders exist, keep the newest and delete the rest.
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


def _extract_timestamp(name: str) -> int:
    match = re.search(rf'{re.escape(FOLDER_PREFIX)}(\d+)', name)
    if match:
        return int(match.group(1))
    return 0


def _delete_folder(path: str) -> None:
    print(f"Deleting stale folder: {path}", flush=True)
    _run(["mega-rm", "-r", path])


def _find_active_folder() -> str:
    """
    Find the active folder:
    1. If FOLDER_FIXED_NAME exists, use it.
    2. Else, get all folders starting with FOLDER_PREFIX.
    3. If multiple, keep the newest (based on timestamp) and delete the rest.
    4. Return the name of the kept folder.
    """
    entries = _list_base_dir()

    if FOLDER_FIXED_NAME and FOLDER_FIXED_NAME in entries:
        return FOLDER_FIXED_NAME

    candidates = [e for e in entries if e.startswith(FOLDER_PREFIX)]

    if len(candidates) == 0:
        raise RuntimeError(
            f"No folder starting with '{FOLDER_PREFIX}' found under {MEGA_BASE_DIR}. "
            f"mega-ls returned: {entries}. Check FOLDER_PREFIX in config.py."
        )

    if len(candidates) == 1:
        return candidates[0]

    # Multiple folders found: sort by timestamp descending, keep first (newest)
    candidates_with_ts = [(name, _extract_timestamp(name)) for name in candidates]
    # Remove any with timestamp 0 (shouldn't happen but just in case)
    candidates_with_ts = [(n, ts) for n, ts in candidates_with_ts if ts > 0]
    if not candidates_with_ts:
        raise RuntimeError("No valid timestamp found in folder names.")
    candidates_with_ts.sort(key=lambda x: x[1], reverse=True)
    newest_name = candidates_with_ts[0][0]
    stale = [n for n, _ in candidates_with_ts[1:]]

    print(f"Found {len(candidates)} folders. Keeping newest: {newest_name}", flush=True)
    for stale_name in stale:
        stale_path = f"{MEGA_BASE_DIR}/{stale_name}"
        try:
            # First try to disable export (in case it's still active)
            _run(["mega-export", "-d", stale_path], stdin_input="yes\n")
        except Exception:
            pass  # ignore if already disabled
        _delete_folder(stale_path)

    return newest_name


def rotate_mega_link() -> tuple[str, str, str]:
    """
    Returns: (new_link, old_path, temp_path)
    - old_path: folder to delete if Patreon update succeeds
    - temp_path: folder to delete if Patreon update fails (rollback)
    NOTE: The old folder's export is left active – we disable it only after
    the Patreon update is verified.
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

    # DO NOT disable old export yet – we keep it alive until Patreon succeeds
    print("Step 3/3: old export remains active (safe).", flush=True)

    return link, old_path, temp_path


if __name__ == "__main__":
    print(rotate_mega_link())
