import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "privacy: blocking privacy suite that needs the configured spaCy model"
    )
