"""The committed openapi.json is the API's public contract. If a route's
shape changes without regenerating it (``make openapi``), fail loudly here
instead of letting it drift unnoticed."""

from __future__ import annotations

import json
from pathlib import Path

from issue_to_patch.api.app import app

_COMMITTED = Path(__file__).resolve().parent.parent.parent / "openapi.json"


def test_openapi_json_matches_the_live_schema() -> None:
    committed = json.loads(_COMMITTED.read_text("utf-8"))
    live = app.openapi()
    assert committed == live, (
        "openapi.json is out of date with the app's routes/schemas — "
        "run `make openapi` and commit the result"
    )
