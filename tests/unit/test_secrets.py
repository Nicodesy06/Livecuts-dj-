import sys

from dj_director.config.secrets import SecretStore


def test_set_get_and_remove(tmp_path):
    s = SecretStore(tmp_path)
    assert s.get("obs_password") == ""
    s.set("obs_password", "pässwörd")
    assert SecretStore(tmp_path).get("obs_password") == "pässwörd"
    s.set("obs_password", "")
    assert s.get("obs_password") == ""


def test_not_stored_in_plain_text(tmp_path):
    s = SecretStore(tmp_path)
    s.set("obs_password", "very-secret-value")
    raw = (tmp_path / "secrets.json").read_text()
    assert "very-secret-value" not in raw
    assert ("dpapi" in raw) == (sys.platform == "win32")


def test_corrupt_store_is_ignored(tmp_path):
    (tmp_path / "secrets.json").write_text("garbage")
    assert SecretStore(tmp_path).get("obs_password") == ""
