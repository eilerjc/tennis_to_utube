from tennis_to_utube import config
from tennis_to_utube.config import effective_settings, load_config


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.path is None and cfg.warnings == []
    assert cfg.get("lead_in_ms.default") == 5000
    assert cfg.get("chapter_gap_ms") == 600_000
    assert cfg.get("tools.ffmpeg") == "ffmpeg"


def test_user_file_overrides_only_given_keys(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('chapter_gap_ms = 300000\n[tools]\nffmpeg = "C:/ffmpeg/bin/ffmpeg.exe"\n'
                 '[lead_in_ms]\nace = 3500\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.warnings == []
    assert cfg.get("chapter_gap_ms") == 300000
    assert cfg.get("tools.ffmpeg") == "C:/ffmpeg/bin/ffmpeg.exe"
    assert cfg.get("tools.ffprobe") == "ffprobe"
    assert cfg.get("lead_in_ms") == {"default": 5000, "ace": 3500}
    # defaults are not mutated
    assert config.DEFAULTS["chapter_gap_ms"] == 600_000


def test_bad_values_warn_and_keep_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('chapter_gap_ms = "ten minutes"\ntools = 3\nfrobnicate = true\n'
                 '[lead_in_ms]\nace = "short"\n[playback]\nspeeds = [0.5, 1]\nskip_short_ms = 1.5\n',
                 encoding="utf-8")
    cfg = load_config(p)
    assert cfg.get("chapter_gap_ms") == 600_000
    assert cfg.get("tools.ffmpeg") == "ffmpeg"
    assert "ace" not in cfg.get("lead_in_ms")
    assert cfg.get("playback.speeds") == [0.5, 1]
    assert cfg.get("playback.skip_short_ms") == 1000
    assert cfg.get("frobnicate") is True  # kept, but flagged
    text = "\n".join(cfg.warnings)
    for needle in ("chapter_gap_ms", "tools", "frobnicate", "lead_in_ms.ace", "skip_short_ms"):
        assert needle in text


def test_invalid_toml_still_starts(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is = = not toml", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.get("lead_in_ms.default") == 5000
    assert len(cfg.warnings) == 1


def test_default_location_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_CONFIG_DIR, str(tmp_path))
    (tmp_path / "config.toml").write_text("chapter_gap_ms = 1000\n", encoding="utf-8")
    assert config.user_config_dir() == tmp_path
    assert load_config().get("chapter_gap_ms") == 1000


def test_windows_location(monkeypatch):
    monkeypatch.delenv(config.ENV_CONFIG_DIR, raising=False)
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:\\Users\\me\\AppData\\Roaming")
    assert str(config.user_config_dir()).endswith("tennis_to_utube")
    assert "Roaming" in str(config.user_config_dir())


def test_match_settings_override_app_config(tmp_path):
    cfg = load_config(tmp_path / "none.toml")
    s = effective_settings(cfg, {"lead_in_ms": {"ace": 3000, "default": 6000}, "chapter_gap_ms": 120000})
    assert s.lead_in_for("ace") == 3000
    assert s.lead_in_for("point") == 6000
    assert s.chapter_gap_ms == 120000
    assert effective_settings(cfg).lead_in_for("ace") == 5000
