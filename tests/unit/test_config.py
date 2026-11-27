import pytest

from ops_agent.config import load_config_from_env, parse_max_retries, parse_port, parse_timeout


def test_parse_port_accepts_valid_value() -> None:
    assert parse_port("8000") == 8000


@pytest.mark.parametrize("bad_value", ["abc", "0", "65536"])
def test_parse_port_rejects_invalid_values(bad_value: str) -> None:
    with pytest.raises(ValueError):
        parse_port(bad_value)


def test_parse_timeout_accepts_valid_value() -> None:
    assert parse_timeout("30") == 30


@pytest.mark.parametrize("bad_value", ["slow", "0", "301"])
def test_parse_timeout_rejects_invalid_values(bad_value: str) -> None:
    with pytest.raises(ValueError):
        parse_timeout(bad_value)


def test_parse_max_retries_boundaries() -> None:
    assert parse_max_retries("0") == 0
    assert parse_max_retries("5") == 5
    with pytest.raises(ValueError):
        parse_max_retries("6")


def test_load_config_from_env_defaults_and_overrides() -> None:
    cfg = load_config_from_env(
        {
            "APP_ENV": "prod",
            "APP_PORT": "9000",
            "LLM_TIMEOUT_SECONDS": "45",
            "LLM_MAX_RETRIES": "3",
        }
    )
    assert cfg.app_env == "prod"
    assert cfg.app_port == 9000
    assert cfg.llm_timeout_seconds == 45
    assert cfg.llm_max_retries == 3
