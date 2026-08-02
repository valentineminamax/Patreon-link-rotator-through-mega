"""
Rotates the MEGA public link by copying your video folder to a
freshly, uniquely named folder each run, inside a fixed outer folder
(e.g. "Patreon Content").

Why a fresh name instead of alternating between two fixed names:
A MEGA link is https://mega.nz/folder/<handle>#<key>. Both belong to
that specific folder's node and do NOT change just from disabling and
re-enabling the export -- so toggling export on the same folder would
likely hand back the exact same link. Copying to a new folder gives
it a new node (new handle), which guarantees a new link.

An earlier version of this alternated between two fixed names
("patreon-1" / "patreon-2"). That broke badly: if the old-folder
cleanup step ever failed silently (which it did), both names would
exist at once, the "find active folder" logic had no way to tell
which one was actually current, and it kept picking the same stale
one -- causing mega-cp to nest copies INSIDE the other folder instead
of creating a clean new one, run after run. Timestamped names plus
the single-folder invariant below close that hole: there is only ever
one legal folder, and if that's ever violated, this script refuses to
guess and stops instead of silently making a mess.

Flow each run:
  1. List MEGA_BASE_DIR. Exactly one folder starting with
     FOLDER_PREFIX must exist -- that's "the active folder". If zero
     or more than one exist, STOP and raise (see docstring on
     _find_active_folder for why more-than-one is treated as fatal,
     not "pick one").
  2. mega-cp: server-side copy that whole folder to a brand new,
     timestamped name. If this fails, it raises and nothing below
     runs -- your videos are untouched.
  3. Only now is it safe to kill the old export + delete the old
     folder (this is what makes the leaked link go dead). If this
     step fails, we raise loudly rather than continue, because a
     failed cleanup leaves the OLD link potentially still live, which
     defeats the entire purpose of this bot.
  4. mega-export -a on the new folder -> fresh link.

Requires: megacmd installed and already logged in
(`mega-login <email> <password>`) before this runs.

STRONGLY RECOMMENDED: before pointing this at your real videos, test
it against a throwaway folder with a couple of junk files, and
confirm in the MEGA app that the old one is really gone and the new
one has everything.
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


def _find_active_folder() -> str:
    entries = _list_base_dir()
    candidates = [e for e in entries if e.startswith(FOLDER_PREFIX)]

    if len(candidates) == 0:
        raise RuntimeError(
            f"No folder starting with '{FOLDER_PREFIX}' found under {MEGA_BASE_DIR}. "
            f"mega-ls returned: {entries}. Check FOLDER_PREFIX in config.py."
        )

    if len(candidates) > 1:
        # This is the exact situation that caused nested duplicate
        # copies before: a previous run's cleanup step failed and left
        # a stale folder behind. Guessing which one is "real" here
        # risks corrupting things further -- stop and make the human
        # look, instead.
        raise RuntimeError(
            f"Found {len(candidates)} folders matching '{FOLDER_PREFIX}*' under "
            f"{MEGA_BASE_DIR}: {candidates}. This means a previous run's cleanup "
            "step failed and left an old folder behind -- rotating again right now "
            "would risk creating nested duplicate copies instead of a clean rotation "
            "(this has happened before). Manually check the MEGA app: confirm which "
            "folder has the real current content, delete the stale one(s) yourself, "
            "and double-check none of them still has a live export "
            "(`mega-export -s <path>`) before re-running."
        )

    return candidates[0]


def rotate_mega_link() -> str:
    active_name = _find_active_folder()
    next_name = f"{FOLDER_PREFIX}{int(time.time())}"

    old_path = f"{MEGA_BASE_DIR}/{active_name}"
    new_path = f"{MEGA_BASE_DIR}/{next_name}"

    print(f"Step 1/4: copying {old_path} -> {new_path} ...", flush=True)
    # 1. Server-side copy the folder to a new node.
    #    Note: mega-cp has no -r flag — it copies folders recursively
    #    by default. (mega-rm below DOES use -r, that one's correct.)
    _run(["mega-cp", old_path, new_path])
    print("Step 1/4: copy done.", flush=True)

    print(f"Step 2/4: disabling export on {old_path} ...", flush=True)
    # 2. Now safe to retire the old folder + its old export.
    #    "not exported" is a legitimate, harmless outcome (nothing to
    #    disable) -- anything else is unexpected and we stop rather
    #    than risk leaving a live link behind.
    #    Note: MEGA's official docs example shows -d does NOT prompt
    #    for anything (unlike -a) -- it just disables and prints
    #    immediately. The stdin below is harmless leftover insurance,
    #    not believed to be doing anything on this call.
    export_d = subprocess.run(
        ["mega-export", "-d", old_path], capture_output=True, text=True, timeout=60, input="yes\n"
    )
    combined = (export_d.stdout + export_d.stderr).lower()
    if export_d.returncode != 0 and "not exported" not in combined:
        raise RuntimeError(
            f"Failed to disable export on {old_path} for an unexpected reason -- "
            f"refusing to continue since the old link may still be live.\n"
            f"stdout: {export_d.stdout}\nstderr: {export_d.stderr}"
        )
    print("Step 2/4: export disabled (or already wasn't exported).", flush=True)

    print(f"Step 3/4: deleting {old_path} ...", flush=True)
    rm_r = subprocess.run(["mega-rm", "-r", old_path], capture_output=True, text=True, timeout=120)
    if rm_r.returncode != 0:
        raise RuntimeError(
            f"Failed to delete old folder {old_path} after the copy to {new_path} "
            "already succeeded -- your fresh copy and content are safe, but the OLD "
            "folder is still sitting there and must be cleaned up manually before the "
            f"next run, or duplicates will pile up again.\nstderr: {rm_r.stderr}"
        )
    print("Step 3/4: old folder deleted.", flush=True)
    print("Step 4/4: generating new export link ...", flush=True)

    # 3. Export the new folder -> fresh link.
    # MEGA's official docs show -a can trigger TWO separate sequential
    # confirmation prompts on an account's first-ever export (a
    # copyright-terms Yes/No, immediately followed by a second
    # yes/no/all/none prompt) -- not just one. Piping only one "yes\n"
    # answers the first prompt and then leaves the second one reading
    # from an already-closed stdin, which is the leading suspect for
    # the multi-minute hangs we saw. Piping several yes answers covers
    # both known prompts plus margin for a third if there is one.
    result = _run(["mega-export", "-a", new_path], stdin_input="yes\nyes\nyes\n")
    for line in result.stdout.splitlines():
        if "https://mega.nz" in line:
            link = line.strip().split()[-1]
            print(f"Step 4/4: new link generated: {link}", flush=True)
            return link

    raise RuntimeError("Could not parse new MEGA link from mega-export output:\n" + result.stdout)


if __name__ == "__main__":
    print(rotate_mega_link())
