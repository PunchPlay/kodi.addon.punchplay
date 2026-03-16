"""
PunchPlay Scrobble — entry point.

Kodi runs this file in two contexts:
  1. Service start (no args)  → launches the background monitor loop.
  2. Settings button action   → RunAddon(script.punchplay, login|logout)
                                handles the device-code flow or clears tokens.
"""

import sys
import os

# Add the bundled lib/ directory to the path so that guessit (and its
# dependencies) can be imported from inside the addon zip.
_addon_dir = os.path.dirname(os.path.abspath(__file__))
_lib_dir = os.path.join(_addon_dir, "lib")
if os.path.isdir(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)


def main() -> None:
    args = [a for a in sys.argv[1:] if a]
    action = args[0].lower() if args else None

    if action == "login":
        from cache import Cache
        from api import APIClient

        client = APIClient(cache=Cache())
        client.device_code_login()

    elif action == "logout":
        from api import APIClient

        APIClient().logout()

    else:
        # No arguments → running as the background service.
        from service import PunchPlayService

        PunchPlayService().run()


main()
