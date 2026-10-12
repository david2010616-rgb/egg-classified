"""Keep private application state outside downloadable/publishable source folders."""

import os
import sys
from pathlib import Path


def data_dir():
    override = os.getenv("LAFC_DATA_DIR")
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LocalAIFileClassifier"
    return Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "local-ai-file-classifier"
