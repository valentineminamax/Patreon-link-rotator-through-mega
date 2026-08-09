# config.py – MEGA folder settings
# Edit these to match your MEGA cloud structure.

# The remote root folder where your active folder lives.
# Example: "/Root" (if your folder is at the top level)
# Or: "/MyFiles/Data"
MEGA_BASE_DIR = "Patreon Content"   # <-- CHANGE THIS

# Prefix used for timestamped folders (e.g., "patreon-").
# The script looks for folders starting with this prefix.
FOLDER_PREFIX = "patreon-"

# Fixed name of the folder that is always the active one.
# If you keep a folder with a constant name (e.g., "active"),
# set this. If you only use timestamped folders, leave as empty string.
FOLDER_FIXED_NAME = "Mina Valentine Patreon"   # or "" if you don't use a fixed name
