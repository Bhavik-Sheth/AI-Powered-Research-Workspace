import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# The four suites test pure functions, but importing their package (e.g.
# `provenance`, which imports `db.models`) also imports `db/__init__.py`,
# which reads `BEARER_TOKEN` at module load to construct an (unconnected —
# SQLAlchemy engines are lazy) async engine. No test here ever issues a
# query, so a placeholder value is enough to let the import succeed.
os.environ.setdefault("BEARER_TOKEN", "pytest-placeholder")
