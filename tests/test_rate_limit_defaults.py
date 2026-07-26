from blockhost_backend.config.config_manager import Settings


def test_dev_settings_use_higher_auth_rate_limit() -> None:
    settings = Settings(_env_file=None)

    assert settings.rate_limit_auth_per_minute > 10
    assert settings.rate_limit_upload_per_minute > 10
