import pytest

from app.config import ConfigError, load_dotenv, load_settings


def test_defaults_are_safe():
    s = load_settings({})
    assert s.host == "127.0.0.1"
    assert s.port == 8080
    assert s.sync_interval_hours == 6
    assert s.fetch_on_startup is True
    assert s.max_jobs_per_source == 500
    assert s.request_timeout_seconds == 20
    assert s.auth_enabled is False


@pytest.mark.parametrize(
    "env",
    [
        {"JOBHUB_PORT": "99999"},
        {"JOBHUB_PORT": "abc"},
        {"JOBHUB_SYNC_INTERVAL_HOURS": "0.1"},
        {"JOBHUB_FETCH_ON_STARTUP": "maybe"},
        {"JOBHUB_LOG_LEVEL": "LOUD"},
        {"JOBHUB_REQUEST_TIMEOUT_SECONDS": "0"},
        {"JOBHUB_MAX_JOBS_PER_SOURCE": "0"},
        {"JOBHUB_GREENHOUSE_BOARDS": "good,../../etc"},
        {"JOBHUB_LEVER_SITES": "a b"},
        {"JOBHUB_AUTH_USER": "me"},
        {"JOBHUB_AUTH_USER": "me", "JOBHUB_AUTH_PASSWORD": "short"},
        {"JOBHUB_ALLOWED_HOSTS": " , "},
    ],
)
def test_invalid_configuration_rejected(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_board_lists_and_bools_parsed():
    s = load_settings({"JOBHUB_GREENHOUSE_BOARDS": "gitlab, stripe", "JOBHUB_SOURCE_REMOTIVE": "off",
                       "JOBHUB_AUTH_USER": "admin", "JOBHUB_AUTH_PASSWORD": "a-long-password-1"})
    assert s.greenhouse_boards == ["gitlab", "stripe"]
    assert s.enable_remotive is False
    assert s.auth_enabled


def test_dotenv_never_overrides_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("JOBHUB_TEST_VALUE=fromfile\nJOBHUB_OTHER=x\n")
    monkeypatch.setenv("JOBHUB_TEST_VALUE", "fromenv")
    monkeypatch.delenv("JOBHUB_OTHER", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["JOBHUB_TEST_VALUE"] == "fromenv"
    assert os.environ["JOBHUB_OTHER"] == "x"
    assert env_file.read_text().startswith("JOBHUB_TEST_VALUE=fromfile")  # file untouched
    monkeypatch.delenv("JOBHUB_OTHER")


def test_short_password_error_is_actionable():
    with pytest.raises(ConfigError) as exc:
        load_settings({"JOBHUB_AUTH_USER": "omkar", "JOBHUB_AUTH_PASSWORD": "Secret"})
    msg = str(exc.value)
    assert "received 6" in msg and "single quotes" in msg and "leave both" in msg
    assert "Secret" not in msg  # never echo the secret
    with pytest.raises(ConfigError, match="JOBHUB_AUTH_PASSWORD is empty"):
        load_settings({"JOBHUB_AUTH_USER": "omkar"})
