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
    d = c.durations()
    assert (d.min, d.pref_min, d.pref_max, d.hard_cap, d.cooldown) == (5.0, 6.0, 10.0, 0.0, 20.0)


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


# ----------------------------------------------------------------------------- compatibility (0.1.x -> 0.1.3)
LEGACY_012 = {
    "schema_version": 1, "first_run_done": True, "shadow_mode": False,
    "obs": {"host": "127.0.0.1", "port": 4455, "auto_connect": True, "connect_timeout": 5.0, "request_timeout": 3.0},
    "audio": {"backend": "input", "device_name": "Microfono (USB Audio CODEC )", "host_api": "Windows WASAPI",
              "channels": [0, 1], "sample_rate": 0, "block_size": 512, "file_path": "", "file_loop": True},
    "analysis": {"silence_db": -55.0, "min_bpm": 70.0, "max_bpm": 180.0, "prior_bpm": 124.0, "beats_per_bar": 4,
                 "sensitivity": 1.0},
    "director": {"preset": "natural", "overrides": {"variety_jitter": 0.2, "duration_spread": 0.4, "hysteresis": 0.3},
                 "override_policy": "timed", "override_seconds": 30.0, "no_signal_policy": "master",
                 "latency_offset_ms": 60.0, "seed": 0},
    "cameras": [
        {"scene_name": "Cam Master", "scene_uuid": "u1", "role": "MASTER", "enabled": True, "is_master": True,
         "fallback": False, "weight": 1.0, "priority": 5, "min_duration": 0.0, "mean_duration": 0.0,
         "max_duration": 0.0, "cooldown": -1.0, "allowed_states": []},
        {"scene_name": "Cam Mixer", "scene_uuid": "u2", "role": "DETAIL", "enabled": True, "is_master": False,
         "fallback": False, "weight": 1.2, "priority": 7, "min_duration": 0.0, "mean_duration": 9.0,
         "max_duration": 15.0, "cooldown": 25.0, "allowed_states": ["stable_groove", "transition_candidate"]},
    ],
    "log_decisions": True, "ui_language": "auto",
}


def test_v012_config_loads_without_issues(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(LEGACY_012), "utf-8")
    m = ConfigManager(p)
    cfg = m.load()
    assert m.issues == []
    assert cfg.director.overrides == {"variety_jitter": 0.2, "duration_spread": 0.4, "hysteresis": 0.3}
    assert cfg.director.latency_offset_ms == 60.0 and cfg.cameras[1].allowed_states == ["stable_groove",
                                                                                         "transition_candidate"]


def test_legacy_mean_and_max_map_to_preferred_window_and_cap():
    from dj_director.director.cameras import CameraRegistry
    from dj_director.director.engine import DirectorEngine
    cfg = from_dict(AppConfig, LEGACY_012)
    d = cfg.cameras[1].durations()
    assert (round(d.pref_min, 1), round(d.pref_max, 1), d.hard_cap, d.cooldown) == (7.2, 10.8, 15.0, 25.0)
    eng = DirectorEngine(CameraRegistry.from_configs(cfg.cameras), resolve_params(cfg.director))
    sp = eng.spec(eng.reg.get("Cam Mixer"))
    assert sp.cap == 15.0 and sp.pref_max <= 14.0 and sp.cls == "detail"
    assert eng.spec(eng.reg.get("Cam Master")).cap == float("inf")


def test_new_fields_roundtrip_and_behind_role(tmp_path):
    m = ConfigManager(tmp_path / "config.json")
    cfg = AppConfig(cameras=[CameraConfig("A", role=CameraRole.MASTER, is_master=True),
                             CameraConfig("B", role=CameraRole.BEHIND, preferred_min=11, preferred_max=16,
                                          hard_cap=24)])
    cfg.director.overrides = {"coverage_master": 0.65, "detail_hard_cap": 13.0, "editorial_cuts": False}
    m.save(cfg)
    back = m.load()
    assert m.issues == [] and to_dict(back) == to_dict(cfg)
    assert back.cameras[1].coverage_class == "side"


def test_new_parameters_are_clamped_to_their_ranges():
    issues = []
    cfg = from_dict(AppConfig, {"director": {"overrides": {"detail_hard_cap": 30, "side_hard_cap": 5,
                                                           "coverage_window": 10}}}, issues)
    assert cfg.director.overrides == {"detail_hard_cap": 15.0, "side_hard_cap": 20.0, "coverage_window": 60.0}
    assert len(issues) == 3
