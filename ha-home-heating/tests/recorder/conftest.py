import pytest


@pytest.fixture(autouse=True)
def recorder_then_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder has to start before Home Assistant itself."""
    yield
