import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from synth import have_ffmpeg  # noqa: E402


def pytest_collection_modifyitems(config, items):
    if have_ffmpeg():
        return
    skip = pytest.mark.skip(reason="needs ffmpeg/ffprobe with libx265")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)
