"""D37 boundary — enforced by CI rather than by memory (Voice Layer Plan,
Voice.8): `backend/voice/` is the only package that may import an STT/TTS
or audio-codec library, and `frontend/src/voice/` is the only place that
may touch `getUserMedia` or an `Audio` element. Static-analysis checks, not
a live app — the boundary is exactly the thing you cannot see by using the
app, and it rots silently otherwise.
"""

import ast
import re
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_SRC_ROOT = _BACKEND_ROOT.parent / "frontend" / "src"

# The engine libraries D37 confines to backend/voice/: faster-whisper's own
# package, its underlying inference engine, Piper, and PyAV (V4's decoder).
_BANNED_MODULES = {"faster_whisper", "ctranslate2", "piper", "av"}


def _imported_top_level_modules(source: str) -> set[str]:
    """Every top-level module name a file imports — `import x.y` and
    `from x.y import z` both count as importing `x` (a package's `__init__`
    always runs first), absolute imports only (`node.level == 0` excludes
    `from . import sibling`, which can never reach outside its own package)."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_no_stt_tts_import_outside_voice_package():
    offenders = {}
    for path in _BACKEND_ROOT.rglob("*.py"):
        relative = path.relative_to(_BACKEND_ROOT)
        if ".venv" in relative.parts or "__pycache__" in relative.parts:
            continue
        if relative.parts[0] == "voice":
            continue  # the one module allowed to (backend/voice/, D37)
        banned = _imported_top_level_modules(path.read_text()) & _BANNED_MODULES
        if banned:
            offenders[str(relative)] = banned
    assert not offenders, f"STT/TTS/codec import found outside backend/voice/: {offenders}"


_MEDIA_CAPTURE_PATTERN = re.compile(r"getUserMedia|\bnew Audio\(")


def test_no_media_capture_outside_voice_module():
    offenders = []
    for path in _FRONTEND_SRC_ROOT.rglob("*.ts*"):
        relative = path.relative_to(_FRONTEND_SRC_ROOT)
        if relative.parts[0] == "voice":
            continue  # the one module allowed to (frontend/src/voice/, D37)
        if _MEDIA_CAPTURE_PATTERN.search(path.read_text()):
            offenders.append(str(relative))
    assert not offenders, f"getUserMedia/Audio used outside frontend/src/voice/: {offenders}"
