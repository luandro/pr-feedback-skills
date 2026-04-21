from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPTS = ROOT / "fetch-pr-unresolved-feedback" / "scripts"
RESOLVE_SCRIPTS = ROOT / "resolve-pr-feedback" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"

for path in (str(FETCH_SCRIPTS), str(RESOLVE_SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)


def load_fixture_json(*parts: str) -> object:
    return json.loads((FIXTURES / Path(*parts)).read_text(encoding="utf-8"))


@pytest.fixture
def sample_thread_feedback() -> dict:
    return {
        "unresolved_review_threads": [
            {
                "thread_id": "PRRT_1",
                "root_comment_id": 123,
                "path": "src/app.ts",
                "line": 10,
            }
        ],
        "conversation_comments": [],
    }
