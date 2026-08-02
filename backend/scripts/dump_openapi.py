"""Dumps the FastAPI app's OpenAPI schema to JSON, without starting a server.

Not a domain module — a one-shot codegen input for packages/api-client,
invoked by its `generate` script (Rules.md: the client is regenerated from
the backend's own schema on every backend change).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app  # noqa: E402

if __name__ == "__main__":
    json.dump(app.openapi(), sys.stdout, indent=2)
