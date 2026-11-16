import pytest
from pydantic import ValidationError

from roracle.config import Settings


def test_chat_models_must_be_non_empty(settings):
    base = settings.model_dump()
    assert Settings(**{**base, "chat_models": "a, b"}).chat_model_list == ["a", "b"]
    for bad in ("", " , "):
        with pytest.raises(ValidationError):
            Settings(**{**base, "chat_models": bad})
