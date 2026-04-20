from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPTS = ROOT / "fetch-pr-unresolved-feedback" / "scripts"
RESOLVE_SCRIPTS = ROOT / "resolve-pr-feedback" / "scripts"

for path in (str(FETCH_SCRIPTS), str(RESOLVE_SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)


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
