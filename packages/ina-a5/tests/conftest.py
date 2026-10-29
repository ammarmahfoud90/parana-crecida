from __future__ import annotations

import pytest
import requests

from a5fakes import build_client


@pytest.fixture
def make_client():
    """Build a client over a scripted session. See ``a5fakes.build_client``."""
    return build_client


@pytest.fixture
def timeout_error() -> requests.RequestException:
    return requests.ConnectTimeout("connect timed out")
