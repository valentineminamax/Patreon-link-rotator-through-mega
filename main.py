import asyncio
import subprocess
import time
from mega_rotate import rotate_mega_link
from patreon_update import update_patreon_link


async def main():
    # Rotate MEGA: copy, disable old export, create new link
    new_link, old_path, new_path = rotate_mega_link()
    print(f"New MEGA link: {new_link}")

    # Try to update Patreon
    try:
        await update_patreon_link(new_link)
        print("✅ Patreon post updated successfully.")

        # SUCCESS: Delete the OLD folder
        print(f"Step 4/4: deleting old folder {old_path} ...", flush=True)
        rm_r = subprocess.run(
            ["mega-rm", "-r", "-f", old_path],
            capture_output=True,
            text=True,
            timeout=120,
            input="yes\n"
        )
        if rm_r.returncode != 0:
            print(f"⚠️ WARNING: Failed to delete old folder {old_path} – manual cleanup needed.\n"
                  f"   stderr: {rm_r.stderr}", flush=True)
        else:
            print("✅ Old folder deleted.", flush=True)

    except Exception as e:
        # FAILURE: Delete the NEW folder (rollback)
        print(f"❌ Patreon update failed: {e}", flush=True)
        print(f"   Rolling back: deleting new folder {new_path} ...", flush=True)

        # Disable export on the new folder (if it has one)
        export_d = subprocess.run(
            ["mega-export", "-d", new_path],
            capture_output=True,
            text=True,
            timeout=60,
            input="yes\n"
        )
        if export_d.returncode != 0 and "not exported" not in (export_d.stdout + export_d.stderr).lower():
            print(f"   WARNING: Could not disable export on {new_path} – continuing anyway.", flush=True)

        # Delete the new folder
        rm_r = subprocess.run(
            ["mega-rm", "-r", "-f", new_path],
            capture_output=True,
            text=True,
            timeout=120,
            input="yes\n"
        )
        if rm_r.returncode != 0:
            print(f"❌ CRITICAL: Failed to delete new folder {new_path} – manual cleanup needed.\n"
                  f"   stderr: {rm_r.stderr}", flush=True)
            raise
        else:
            print(f"✅ New folder {new_path} deleted. The old link remains active.", flush=True)
            raise  # Re-raise the original exception so the workflow fails


if __name__ == "__main__":
    asyncio.run(main())
