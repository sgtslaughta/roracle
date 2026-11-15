from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from roracle.api.app import create_app

REPO_ROOT = Path(__file__).parents[2]


@pytest.fixture
def client():
    return TestClient(create_app())
