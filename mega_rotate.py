"""
Rotates a MEGA public share link.

IMPORTANT — why this isn't just "disable export, re-enable export":
A MEGA link is https://mega.nz/file/<handle>#<key>. Both the handle and
the key belong to that specific file's node and do NOT change just from
toggling the export status. So disabling and re-enabling the export on
the SAME file would very likely hand back the exact same link — useless
for rotation.

To get an actually different link, the file needs to become a different
node. This does that WITHOUT re-uploading the video (slow, wastes
bandwidth):

  1. mega-cp:      server-side copy of the file to a temp name
                    (new node -> will get a new handle/key when exported)
  2. mega-export -d + mega-rm on the OLD file: kills the old export and
     deletes the old node entirely, so the leaked link points at nothing
  3. mega-mv:      rename the copy back to the canonical filename
  4. mega-export -a: export the (now-renamed) file -> fresh link

Nothing is deleted until step 1 has already succeeded, so a failed copy
can't wipe out your only copy of the file.

Requires: megacmd installed and already logged in
(`mega-login <email> <password>`) before this runs.

STRONGLY RECOMMENDED: test this manually against a throwaway file first
(not your real video) to confirm it behaves as expected on your account
before trusting it with real content.
"""

import subprocess

from config import MEGA_SOURCE_PATH

TMP_SUFFIX = ".rotating.tmp"


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def rotate_mega_link() -> str:
    tmp_path = MEGA_SOURCE_PATH + TMP_SUFFIX

    # 1. Server-side copy to a new node. If this fails, it raises and
    #    nothing below runs — the original file is untouched.
    _run(["mega-cp", MEGA_SOURCE_PATH, tmp_path])

    # 2. Now it's safe to retire the old node.
    subprocess.run(["mega-export", "-d", MEGA_SOURCE_PATH], check=False)
    subprocess.run(["mega-rm", MEGA_SOURCE_PATH], check=False)

    # 3. Put the copy back at the canonical path/name.
    _run(["mega-mv", tmp_path, MEGA_SOURCE_PATH])

    # 4. Export it -> new link.
    result = _run(["mega-export", "-a", MEGA_SOURCE_PATH])
    for line in result.stdout.splitlines():
        if "https://mega.nz" in line:
            return line.strip().split()[-1]

    raise RuntimeError(
        "Could not parse new MEGA link from mega-export output:\n" + result.stdout
    )


if __name__ == "__main__":
    print(rotate_mega_link())
