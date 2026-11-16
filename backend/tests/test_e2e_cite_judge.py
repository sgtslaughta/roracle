"""Real-model e2e: the citation judge against Ollama. Asserts verdict structure/direction on obvious cases.

Run: ROR_E2E=1 pytest -m e2e tests/test_e2e_cite_judge.py (needs Ollama on localhost:21434 with qwen2.5:14b)."""

import os

import pytest
from openai import OpenAI

from roracle.writing.cite_check import judge

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("ROR_E2E") != "1", reason="set ROR_E2E=1 to run"),
]

PASSAGE = (
    "We trained the model on 12,000 labelled chest X-rays and report a sensitivity of 94% for pneumonia "
    "at a specificity of 91% on the held-out hospital cohort."
)


def test_judge_real_model():
    llm = OpenAI(base_url="http://localhost:21434/v1", api_key="ollama", timeout=120)
    ok, _ = judge(
        llm, "qwen2.5:14b", "The model reached 94% sensitivity for pneumonia on held-out data", PASSAGE
    )
    bad, _ = judge(llm, "qwen2.5:14b", "The authors prove that P equals NP using a quantum annealer", PASSAGE)
    assert ok == "supported"
    assert bad in ("unsupported", "unclear")
