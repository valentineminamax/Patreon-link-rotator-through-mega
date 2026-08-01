"""
Rotates the MEGA public link by alternating your whole video folder
between two names (patreon-1 <-> patreon-2), copying it to a fresh
node each time.

Why alternate names instead of just re-exporting:
A MEGA link is https://mega.nz/folder/<handle>#<key>. Both belong to
that specific folder's node and do NOT change just from disabling and
re-enabling the export -- so toggling export on the same folder would
likely hand back the exact same link. Copying to a new folder gives
it a new node (new handle), which guarantees a new link, and
alternating names also makes it obvious at a glance in the MEGA app
that a rotation actually happened.

Flow each run:
  1. Look at MEGA_BASE_DIR, figure out which of the two names is
     currently active.
  2. mega-cp -r: server-side copy the whole folder to the OTHER name.
     If this fails, it raises and nothing below runs -- your videos
     are untouched.
  3. Only now is it safe to kill the old export + delete the old
     folder (this is what makes the leaked link go dead).
  4. mega-export -a on the new folder -> fresh link.

Requires: megacmd installed and already logged in
(`mega-login <email> <password>`) before this runs.

STRONGLY RECOMMENDED: before pointing this at your real videos, test
it against a throwaway folder with a couple of junk files, and confirm
in the MEGA app that the old folder is really gone and the new one
has everything.
"""

import subprocess

from config import MEGA_BASE_DIR, MEGA_FOLDER_NAMES


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", flush=True)
        print(f"stdout: {result.stdout}", flush=True)
        print(f"stderr: {result.stderr}", flush=True)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def _find_active_folder() -> str:
    result = subprocess.run(
        ["mega-ls", MEGA_BASE_DIR], capture_output=True, text=True
    )
    entries = [line.strip().rstrip("/") for line in result.stdout.splitlines() if line.strip()]

    for name in MEGA_FOLDER_NAMES:
        if name in entries:
            return name

    raise RuntimeError(
        f"Neither {MEGA_FOLDER_NAMES} was found under {MEGA_BASE_DIR}. "
        f"Before the first run, manually create {MEGA_FOLDER_NAMES[0]} "
        "with your videos inside it."
    )


def rotate_mega_link() -> str:
    active_name = _find_active_folder()
    next_name = (
        MEGA_FOLDER_NAMES[1] if active_name == MEGA_FOLDER_NAMES[0] else MEGA_FOLDER_NAMES[0]
    )

    old_path = f"{MEGA_BASE_DIR}/{active_name}"
    new_path = f"{MEGA_BASE_DIR}/{next_name}"

    # 1. Server-side copy the whole folder to a new node.
    _run(["mega-cp", "-r", old_path, new_path])

    # 2. Now safe to retire the old folder + its old export.
    subprocess.run(["mega-export", "-d", old_path], check=False)
    subprocess.run(["mega-rm", "-r", old_path], check=False)

    # 3. Export the new folder -> fresh link.
    result = _run(["mega-export", "-a", new_path])
    for line in result.stdout.splitlines():
        if "https://mega.nz" in line:
            return line.strip().split()[-1]

    raise RuntimeError("Could not parse new MEGA link from mega-export output:\n" + result.stdout)


if __name__ == "__main__":
    print(rotate_mega_link())
