"""Regenerate ``openapi.json`` from the live FastAPI app.

Run this (``make openapi``) whenever a route's shape changes on purpose;
``tests/integration/test_openapi_contract.py`` fails the build otherwise, so
an *accidental* change to the API's public contract is caught in review.
"""

from __future__ import annotations

import json
from pathlib import Path

from issue_to_patch.api.app import app

_OUT = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    schema = app.openapi()
    _OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
