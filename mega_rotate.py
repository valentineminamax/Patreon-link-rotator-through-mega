"""
Rotates the MEGA public link - with rollback and auto-cleanup of stale folders.
The old folder's export is NOT disabled until Patreon update succeeds.
If multiple folders exist, keep the active one and delete the rest.
"""

import subprocess
import time
import re
from config import MEGA_BASE_DIR, FOLDER_PREFIX, FOLDER_FIXED_NAME


def _run(cmd, stdin_input=None, timeout=120):
    result = subprocess.run(
        cmd, capture_output=True, text=True, input=stdin_input, timeout=timeout
    )
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


def _disable_export_quiet(path: str) -> None:
    """Best-effort: disable a folder's export link. Never raises/hangs the run."""
    try:
        _run(["mega-export", "-d", path], stdin_input="yes\n", timeout=60)
    except Exception as e:
        print(f"Note: could not disable export on {path} (may already be disabled): {e}", flush=True)


def _delete_folder(path: str) -> None:
    print(f"Deleting stale folder: {path}", flush=True)
    try:
        _run(["mega-rm", "-r", path], timeout=90)
    except Exception as e:
        # A single stuck/failed stale-folder deletion shouldn't take down the whole rotation.
        print(f"WARNING: failed to delete {path}: {e}", flush=True)


def _find_active_folder() -> str:
    """
    Find the active folder and clean up any stale ones.

    - If FOLDER_FIXED_NAME exists, it IS the active folder. Any leftover
      FOLDER_PREFIX-timestamped folders are stale remnants of a previous
      rotation (e.g. one that crashed before it could rename/delete) and
      are cleaned up here. Previously this branch returned immediately and
      never reached the cleanup logic at all, which is why stale folders
      kept piling up.
    - Otherwise, the newest FOLDER_PREFIX-timestamped folder is treated as
      active, and every other timestamped folder is stale and deleted.
    """
    entries = _list_base_dir()
    candidates = [e for e in entries if e.startswith(FOLDER_PREFIX)]

    if FOLDER_FIXED_NAME and FOLDER_FIXED_NAME in entries:
        active_name = FOLDER_FIXED_NAME
        stale = candidates
    else:
        if len(candidates) == 0:
            raise RuntimeError(
                f"No folder starting with '{FOLDER_PREFIX}' found under {MEGA_BASE_DIR}, "
                f"and FOLDER_FIXED_NAME ('{FOLDER_FIXED_NAME}') was not found either. "
                f"mega-ls returned: {entries}. Check config.py."
            )
        candidates_with_ts = [(name, _extract_timestamp(name)) for name in candidates]
        candidates_with_ts = [(n, ts) for n, ts in candidates_with_ts if ts > 0]
        if not candidates_with_ts:
            raise RuntimeError("No valid timestamp found in folder names.")
        candidates_with_ts.sort(key=lambda x: x[1], reverse=True)
        active_name = candidates_with_ts[0][0]
        stale = [n for n, _ in candidates_with_ts[1:]]

    if stale:
        print(f"Found {len(stale)} stale folder(s) to clean up: {stale}", flush=True)
    for stale_name in stale:
        stale_path = f"{MEGA_BASE_DIR}/{stale_name}"
        _disable_export_quiet(stale_path)
        _delete_folder(stale_path)

    return active_name


def rotate_mega_link() -> tuple[str, str, str]:
    """
    Returns: (new_link, old_path, temp_path)
    - old_path: the previously-active folder, to delete once the Patreon
      update succeeds
    - temp_path: timestamped folder holding the new export; delete it on
      rollback, or pass it to finalize_active_folder() on success
    NOTE: the old folder's export is left active - we disable it only
    after the Patreon update is verified.
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

    # DO NOT disable old export yet - we keep it alive until Patreon succeeds
    print("Step 3/3: old export remains active (safe).", flush=True)

    return link, old_path, temp_path


def finalize_active_folder(temp_path: str) -> str:
    """
    Call this AFTER old_path has been deleted and the Patreon post has
    been verified live with the new link.

    If FOLDER_FIXED_NAME is configured, renames temp_path -> FOLDER_FIXED_NAME
    so the *next* run's _find_active_folder() finds it by fixed name again
    (this is the step that was previously missing entirely).
    Returns the final path of the active folder.
    """
    if not FOLDER_FIXED_NAME:
        return temp_path

    final_path = f"{MEGA_BASE_DIR}/{FOLDER_FIXED_NAME}"
    print(f"Renaming {temp_path} -> {final_path} ...", flush=True)
    _run(["mega-mv", temp_path, final_path])
    return final_path


if __name__ == "__main__":
    print(rotate_mega_link())
