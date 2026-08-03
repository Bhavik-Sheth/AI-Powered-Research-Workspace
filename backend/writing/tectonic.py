"""The Tectonic-in-Docker escape hatch (D34, MODULES.md: "Hides: the
Tectonic-in-Docker escape-hatch invocation"). A one-shot `docker run` per
compile, same shape as Execution Sandbox's `nbclient` fallback but for
typesetting, not user code — this is not a D31 consent-gated execution path
(it runs a fixed compiler binary against LaTeX source, never arbitrary
code), so it runs unconditionally on request rather than behind a
confirmation token.

Tectonic fetches its TeX Live bundle files on demand from its own public
CDN on a cold cache (invariant #3 does not apply: these are open typesetting
resource files, not a paper). `--network none` would make every compile
fail, so this container gets network access; the persistent cache volume
(`.research-os/tectonic-cache/`) keeps that to a one-time cost per machine.
"""

import asyncio
import uuid
from pathlib import Path

import docker
import docker.errors

from settings import get_vault_path

TECTONIC_IMAGE = "research-os-tectonic:latest"

_COMPILE_TIMEOUT_SECONDS = 120

_docker_client: docker.DockerClient | None = None


def _get_docker_client() -> docker.DockerClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


async def compile_tex(tex: str, manuscript_dir: Path) -> tuple[bool, str, bytes | None]:
    """Compiles `tex` with Tectonic, mounting the manuscript's own directory
    (not a scratch folder elsewhere) so `\\includegraphics{assets/...}`
    resolves against the same `assets/` an insert path wrote into. The
    candidate source is written under a uuid-scoped scratch name, never the
    persisted `<slug>.tex`, so a failed compile leaves the real file
    untouched; both the scratch source and its PDF are removed once the
    caller has read the result. Returns `(success, log, pdf_bytes)` —
    `pdf_bytes` is `None` on failure, `log` is the engine's own chatter
    either way (the compile-error panel's content on failure)."""
    cache_dir = get_vault_path() / ".research-os" / "tectonic-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    scratch_name = f".compile-{uuid.uuid4().hex}"
    tex_path = manuscript_dir / f"{scratch_name}.tex"
    pdf_path = manuscript_dir / f"{scratch_name}.pdf"
    tex_path.write_text(tex, encoding="utf-8")

    try:
        docker_client = await asyncio.to_thread(_get_docker_client)
        try:
            container = await asyncio.to_thread(
                docker_client.containers.run,
                TECTONIC_IMAGE,
                command=["--untrusted", "--chatter=minimal", tex_path.name],
                volumes={
                    str(manuscript_dir): {"bind": "/workspace", "mode": "rw"},
                    str(cache_dir): {"bind": "/cache", "mode": "rw"},
                },
                environment={"XDG_CACHE_HOME": "/cache"},
                network_mode="bridge",
                mem_limit="1g",
                detach=True,
            )
        except docker.errors.ImageNotFound as exc:
            return False, f"the {TECTONIC_IMAGE} image is not built — see docker/tectonic.Dockerfile: {exc}", None

        try:
            result = await asyncio.to_thread(container.wait, timeout=_COMPILE_TIMEOUT_SECONDS)
            exit_code = int(result.get("StatusCode", -1))
            log = (await asyncio.to_thread(container.logs)).decode("utf-8", errors="replace")
        finally:
            await asyncio.to_thread(container.remove, force=True)

        if exit_code == 0 and pdf_path.exists():
            return True, log, pdf_path.read_bytes()
        return False, log, None
    finally:
        tex_path.unlink(missing_ok=True)
        pdf_path.unlink(missing_ok=True)
