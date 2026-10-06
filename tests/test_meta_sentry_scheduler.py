import pytest

from app.meta_sentry_scheduler import scheduler_config


def test_meta_scheduler_disabled_by_default_and_bounded():
    config = scheduler_config({})
    assert config["enabled"] is False
    assert config["interval_seconds"] >= 3600
    assert 1 <= config["max_queries"] <= 12


def test_meta_scheduler_can_be_enabled():
    config = scheduler_config({
        "CLUBSP_META_AUTODISCOVERY": "true",
        "CLUBSP_META_DISCOVERY_INTERVAL_SECONDS": "7200",
        "CLUBSP_META_DISCOVERY_MAX_QUERIES": "4",
    })
    assert config == {"enabled": True, "interval_seconds": 7200, "max_queries": 4}


def test_meta_scheduler_refuses_aggressive_polling():
    with pytest.raises(ValueError, match="one hour"):
        scheduler_config({
            "CLUBSP_META_AUTODISCOVERY": "1",
            "CLUBSP_META_DISCOVERY_INTERVAL_SECONDS": "60",
        })
