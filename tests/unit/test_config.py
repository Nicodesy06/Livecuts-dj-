import json

from dj_director.config.manager import ConfigManager
from dj_director.config.presets import PRESETS, resolve_params
from dj_director.config.schema import (AppConfig, CameraConfig, CameraRole, DirectorConfig, from_dict,
                                       to_dict)


def test_missing_config_gives_defaults(tmp_path):
    m = ConfigManager(tmp_path / "config.json")
    cfg = m.load()
    assert isinstance(cfg, AppConfig) and not cfg.first_run_done and m.issues == []


def test_save_load_roundtrip(tmp_path):
    m = ConfigManager(tmp_path / "config.json")
    cfg = AppConfig(first_run_done=True)
    cfg.cameras = [CameraConfig("A", role=CameraRole.MASTER, is_master=True),
                   CameraConfig("B", role=CameraRole.DETAIL, weight=1.5)]
    cfg.director.preset = "dynamic"
    m.save(cfg)
    back = m.load()
    assert to_dict(back) == to_dict(cfg)
    assert m.issues == []


def test_role_default_sentinels_survive_reload(tmp_path):
    m = ConfigManager(tmp_path / "config.json")
    cfg = AppConfig(cameras=[CameraConfig("A", role=CameraRole.DETAIL)])
    m.save(cfg)
    c = m.load().cameras[0]
    assert c.min_duration == 0 and c.cooldown == -1
    assert c.durations() == (5.0, 9.0, 15.0, 20.0)


def test_corrupt_config_is_backed_up_and_reset(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not json", "utf-8")
    m = ConfigManager(p)
    cfg = m.load()
    assert m.recovered_from_corruption
    assert cfg == AppConfig()
    assert list(tmp_path.glob("config.corrupt-*.json"))


def test_invalid_values_are_clamped_or_defaulted():
    issues = []
    cfg = from_dict(AppConfig, {
        "obs": {"port": 999999, "host": 5},
        "audio": {"backend": "carrier-pigeon", "channels": "x"},
        "director": {"override_policy": "never", "overrides": {"cut_threshold": 99, "bogus": 1}},
        "cameras": [{"scene_name": "A", "is_master": True}, {"scene_name": "B", "is_master": True},
                    {"scene_name": "A"}, {"scene_name": ""}],
        "unknown_key": 1,
    }, issues)
    assert cfg.obs.port == 65535 and cfg.obs.host == "127.0.0.1"
    assert cfg.audio.backend == "input" and cfg.audio.channels == [0, 1]
    assert cfg.director.override_policy == "timed"
    assert cfg.director.overrides == {"cut_threshold": 3.0}
    assert [c.scene_name for c in cfg.cameras] == ["A", "B"]
    assert sum(c.is_master for c in cfg.cameras) == 1
    assert len(issues) >= 6


def test_export_contains_no_password(tmp_path, monkeypatch):
    from dj_director.config.secrets import SecretStore
    store = SecretStore(tmp_path)
    store.set("obs_password", "hunter2-secret")
    cfg = AppConfig()
    out = tmp_path / "export.json"
    ConfigManager.export(cfg, out)
    assert "hunter2-secret" not in out.read_text()
    assert "hunter2-secret" not in json.dumps(to_dict(cfg))


def test_presets_are_parameter_sets_of_one_engine():
    assert set(PRESETS) == {"minimal", "natural", "dynamic"}
    m, n, d = PRESETS["minimal"], PRESETS["natural"], PRESETS["dynamic"]
    assert m.max_cuts_per_minute < n.max_cuts_per_minute < d.max_cuts_per_minute
    assert m.master_duration_scale > n.master_duration_scale > d.master_duration_scale
    p = resolve_params(DirectorConfig(preset="natural", overrides={"min_cut_gap": 9.0}))
    assert p.min_cut_gap == 9.0 and p.cut_threshold == n.cut_threshold
    assert resolve_params(DirectorConfig(preset="nonexistent")) == n
