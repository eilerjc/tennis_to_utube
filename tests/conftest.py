import importlib.util
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent))

from synth import have_ffmpeg  # noqa: E402


HAVE_QT = importlib.util.find_spec("PySide6") is not None


def pytest_collection_modifyitems(config, items):
    skip_ffmpeg = pytest.mark.skip(reason="needs ffmpeg/ffprobe with libx265")
    skip_gui = pytest.mark.skip(reason="needs PySide6")
    for item in items:
        if "ffmpeg" in item.keywords and not have_ffmpeg():
            item.add_marker(skip_ffmpeg)
        if "gui" in item.keywords and not HAVE_QT:
            item.add_marker(skip_gui)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Isolated user config dir (state.json, config.toml)."""
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setenv("TENNIS_TO_UTUBE_CONFIG_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def _no_server_menu(request, monkeypatch):
    """GUI tests never open the real "Who serves?" menu (it blocks): the first player is
    picked unless a test replaces ``page.ask_server`` itself."""
    if "gui" in request.keywords and HAVE_QT:
        from tennis_to_utube.gui.mark_page import MarkPage

        monkeypatch.setattr(MarkPage, "ask_server", lambda self, players: players[0] if players else None)
