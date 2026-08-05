"""
Rotates the MEGA public link – with automatic rollback.
If Patreon update fails, the newly created folder is deleted so the old
link stays active.
"""

import subprocess
import time

from config import MEGA_BASE_DIR, FOLDER_PREFIX


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
    """Extract timestamp from folder name like 'patreon-1785868385'."""
    try:
        return int(name.replace(FOLDER_PREFIX, ""))
    except ValueError:
        return 0


def _find_and_cleanup_active_folder() -> tuple[str, str | None]:
    """
    Finds the active folder. If there are two, determines which is old/new,
    deletes the old one (since the new one was already created by a previous
    run that crashed after Patreon update), and returns the new one.
    Returns: (active_folder_name, old_folder_name_that_was_deleted_or_None)
    """
    entries = _list_base_dir()
    candidates = sorted(
        [e for e in entries if e.startswith(FOLDER_PREFIX)],
        key=_extract_timestamp
    )

    if len(candidates) == 0:
        raise RuntimeError(
            f"No folder starting with '{FOLDER_PREFIX}' found under {MEGA_BASE_DIR}. "
            f"mega-ls returned: {entries}. Check FOLDER_PREFIX in config.py."
        )

    if len(candidates) == 1:
        return candidates[0], None

    # Two or more folders – this means a previous run created a new folder
    # but crashed before deleting the old one.
    # The one with the largest timestamp is the newest.
    active_name = candidates[-1]
    old_name = candidates[-2]  # The second-newest is the one to delete

    old_path = f"{MEGA_BASE_DIR}/{old_name}"

    print(f"⚠️ Found {len(candidates)} folders matching '{FOLDER_PREFIX}*' – "
          f"this means a previous run crashed mid-operation.", flush=True)
    print(f"   Newer: {active_name} (will keep)", flush=True)
    print(f"   Older: {old_name} (will delete)", flush=True)

    # Disable export on the old folder (if it has one)
    print(f"   Disabling export on {old_path} ...", flush=True)
    export_d = subprocess.run(
        ["mega-export", "-d", old_path],
        capture_output=True,
        text=True,
        timeout=60,
        input="yes\n"
    )
    if export_d.returncode != 0 and "not exported" not in (export_d.stdout + export_d.stderr).lower():
        print(f"   WARNING: Could not disable export on {old_path} – continuing anyway.", flush=True)

    # Delete the old folder
    print(f"   Deleting old folder {old_path} ...", flush=True)
    rm_r = subprocess.run(
        ["mega-rm", "-r", "-f", old_path],
        capture_output=True,
        text=True,
        timeout=120,
        input="yes\n"
    )
    if rm_r.returncode != 0:
        raise RuntimeError(
            f"Failed to delete old folder {old_path} during recovery.\n"
            f"stderr: {rm_r.stderr}\n"
            "This folder must be manually deleted before the bot can continue."
        )
    print(f"   ✅ Deleted old folder {old_name}.", flush=True)

    return active_name, old_name


def rotate_mega_link() -> tuple[str, str, str]:
    """
    Returns: (new_link, old_path, new_path)
    - old_path: folder to delete if Patreon update succeeds
    - new_path: folder to delete if Patreon update fails (rollback)
    """
    # First, check if we need to recover from a previous crash
    active_name, deleted_name = _find_and_cleanup_active_folder()

    # Now proceed with the normal rotation flow
    next_name = f"{FOLDER_PREFIX}{int(time.time())}"

    old_path = f"{MEGA_BASE_DIR}/{active_name}"
    new_path = f"{MEGA_BASE_DIR}/{next_name}"

    print(f"Step 1/4: copying {old_path} -> {new_path} ...", flush=True)
    _run(["mega-cp", old_path, new_path])
    print("Step 1/4: copy done.", flush=True)

    print(f"Step 2/4: disabling export on {old_path} ...", flush=True)
    export_d = subprocess.run(
        ["mega-export", "-d", old_path],
        capture_output=True,
        text=True,
        timeout=60,
        input="yes\n"
    )
    combined = (export_d.stdout + export_d.stderr).lower()
    if export_d.returncode != 0 and "not exported" not in combined:
        raise RuntimeError(
            f"Failed to disable export on {old_path} for an unexpected reason -- "
            f"refusing to continue since the old link may still be live.\n"
            f"stdout: {export_d.stdout}\nstderr: {export_d.stderr}"
        )
    print("Step 2/4: export disabled (or already wasn't exported).", flush=True)

    print("Step 3/4: generating new export link ...", flush=True)
    result = _run(["mega-export", "-a", new_path], stdin_input="yes\nyes\nyes\n")
    for line in result.stdout.splitlines():
        if "https://mega.nz" in line:
            link = line.strip().split()[-1]
            print(f"Step 3/4: new link generated: {link}", flush=True)
            break
    else:
        raise RuntimeError("Could not parse new MEGA link from mega-export output:\n" + result.stdout)

    # Return both paths so main.py can decide which one to delete
    return link, old_path, new_path


if __name__ == "__main__":
    print(rotate_mega_link())
