import asyncio
import subprocess
import time
from mega_rotate import rotate_mega_link
from patreon_update import update_patreon_link
from config import MEGA_BASE_DIR, FOLDER_FIXED_NAME


async def main():
    # Rotate MEGA: copy, generate new link, keep old active
    new_link, old_path, temp_path = rotate_mega_link()
    print(f"New MEGA link: {new_link}")

    # Update Patreon using API
    try:
        update_patreon_link(new_link)
        print("✅ Patreon post updated successfully.")

        # SUCCESS: Delete old folder, rename temp to fixed name
        print(f"Step 4/4a: disabling export on old folder {old_path} ...", flush=True)
        export_d = subprocess.run(
            ["mega-export", "-d", old_path],
            capture_output=True,
            text=True,
            timeout=60,
            input="yes\n"
        )
        if export_d.returncode != 0 and "not exported" not in (export_d.stdout + export_d.stderr).lower():
            print(f"   WARNING: Could not disable export on {old_path} – continuing anyway.", flush=True)

        print(f"Step 4/4b: deleting old folder {old_path} ...", flush=True)
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

        # Rename temp folder to the fixed name
        fixed_path = f"{MEGA_BASE_DIR}/{FOLDER_FIXED_NAME}"
        print(f"Step 4/4c: renaming {temp_path} -> {fixed_path} ...", flush=True)
        rename_result = subprocess.run(
            ["mega-mv", temp_path, fixed_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        if rename_result.returncode != 0:
            print(f"⚠️ WARNING: Failed to rename temp folder to fixed name.\n"
                  f"   stderr: {rename_result.stderr}", flush=True)
        else:
            print("✅ Folder renamed to fixed name.", flush=True)

    except Exception as e:
        # FAILURE: Delete the TEMP folder (rollback) – old folder stays intact
        print(f"❌ Patreon update failed: {e}", flush=True)
        print(f"   Rolling back: deleting new (temp) folder {temp_path} ...", flush=True)

        export_d = subprocess.run(
            ["mega-export", "-d", temp_path],
            capture_output=True,
            text=True,
            timeout=60,
            input="yes\n"
        )
        if export_d.returncode != 0 and "not exported" not in (export_d.stdout + export_d.stderr).lower():
            print(f"   WARNING: Could not disable export on {temp_path} – continuing anyway.", flush=True)

        rm_r = subprocess.run(
            ["mega-rm", "-r", "-f", temp_path],
            capture_output=True,
            text=True,
            timeout=120,
            input="yes\n"
        )
        if rm_r.returncode != 0:
            print(f"❌ CRITICAL: Failed to delete temp folder {temp_path} – manual cleanup needed.\n"
                  f"   stderr: {rm_r.stderr}", flush=True)
            raise
        else:
            print(f"✅ Temp folder {temp_path} deleted. The old link remains active.", flush=True)
            raise  # Re-raise so the workflow fails


if __name__ == "__main__":
    asyncio.run(main())
