import os
import decky


def _settings_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
