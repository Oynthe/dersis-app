"""Assets package: icons and static resources."""
import os

ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))


def asset_path(filename: str) -> str:
    """Return the absolute path to a bundled asset file."""
    return os.path.join(ASSETS_DIR, filename)
