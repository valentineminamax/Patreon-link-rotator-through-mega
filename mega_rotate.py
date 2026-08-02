"""
Rotates the MEGA public link by alternating your video folder
(e.g. "patreon-1") between two names, copying it to a fresh node
each time, inside a fixed outer folder (e.g. "Patreon Content").

Why alternate names instead of just re-exporting:
A MEGA link is https://mega.nz/folder/<handle>#<key>. Both belong to
that specific folder's node and do NOT change just from disabling and
re-enabling the export -- so toggling export on the same folder would
likely hand back the exact same link. Copying to a new folder gives
it a new node (new handle), which guarantees a new link -- this is
exactly why your manual process re-copies into a fresh folder each
time rather than just re-sharing the same one.

Flow each run:
  1. Look inside MEGA_BASE_DIR, figure out which of the two names is
     currently active.
  2. mega-cp: server-side copy the whole folder to the OTHER name.
     If this fails, it raises and nothing below runs -- your videos
     are untouched.
  3. Only now is it safe to kill the old export + delete the old
     folder (this is what makes the leaked link go dead).
  4. mega-export -a on the new folder -> fresh link.

Requires: megacmd installed and already logged in
(`mega-login <email> <password>`) before this runs.

STRONGLY RECOMMENDED: before pointing this at your real videos, test
it against a throwaway folder with a couple of junk files, and
confirm in the MEGA app that the old one is really gone and the new
one has everything.
"""

import subprocess

from config import MEGA_BASE_DIR, MEGA_FOLDER_NAMES


def _run(cmd, stdin_input=None):
    result = subprocess.run(cmd, capture_output=True, text=True, input=stdin_input, timeout=120)
    if result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", flush=True)
        print(f"stdout: {result.stdout}", flush=True)
        print(f"stderr: {result.stderr}", flush=True)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def _find_active_folder() -> str:
    result = subprocess.run(
        ["mega-ls", MEGA_BASE_DIR], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"mega-ls failed (exit {result.returncode}) — this usually means "
            "you're not logged in (`mega-login`) or MEGAcmd isn't on PATH yet, "
            f"not that the folders are missing.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    entries = [line.strip().rstrip("/") for line in result.stdout.splitlines() if line.strip()]

    for name in MEGA_FOLDER_NAMES:
        if name in entries:
            return name

    raise RuntimeError(
        f"Neither {MEGA_FOLDER_NAMES} was found under {MEGA_BASE_DIR}. "
        f"mega-ls ran fine but returned these entries: {entries}. "
        "Check the exact folder name/spelling in your MEGA account."
    )


def rotate_mega_link() -> str:
    active_name = _find_active_folder()
    next_name = (
        MEGA_FOLDER_NAMES[1] if active_name == MEGA_FOLDER_NAMES[0] else MEGA_FOLDER_NAMES[0]
    )

    old_path = f"{MEGA_BASE_DIR}/{active_name}"
    new_path = f"{MEGA_BASE_DIR}/{next_name}"

    # 1. Server-side copy the folder to a new node.
    #    Note: mega-cp has no -r flag — it copies folders recursively
    #    by default. (mega-rm below DOES use -r, that one's correct.)
    _run(["mega-cp", old_path, new_path])

    # 2. Now safe to retire the old folder + its old export.
    #    Not using _run() here on purpose (a failure to clean up the old
    #    folder shouldn't crash the run when we already have a new link) —
    #    but we still need to know if it happened, since a leaked link
    #    that fails to die is the whole thing this bot exists to prevent.
    export_d = subprocess.run(["mega-export", "-d", old_path], capture_output=True, text=True, timeout=60)
    if export_d.returncode != 0:
        print(f"WARNING: failed to disable export on {old_path}: {export_d.stderr}", flush=True)

    rm_r = subprocess.run(["mega-rm", "-r", old_path], capture_output=True, text=True, timeout=120)
    if rm_r.returncode != 0:
        print(f"WARNING: failed to delete old folder {old_path}: {rm_r.stderr}", flush=True)

    # 3. Export the new folder -> fresh link.
    # stdin_input handles the one-time copyright confirmation prompt
    # MEGA can show on an account's first-ever export.
    result = _run(["mega-export", "-a", new_path], stdin_input="yes\n")
    for line in result.stdout.splitlines():
        if "https://mega.nz" in line:
            return line.strip().split()[-1]

    raise RuntimeError("Could not parse new MEGA link from mega-export output:\n" + result.stdout)


if __name__ == "__main__":
    print(rotate_mega_link())
