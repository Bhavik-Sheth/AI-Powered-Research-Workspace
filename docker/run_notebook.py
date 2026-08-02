"""Runs one notebook top-to-bottom inside the experiment container — the
`nbclient`-under-`docker run` fallback (DECISIONS.md D30's kernel-transport
spike descope). Always executes the whole notebook in cell order top to
bottom; there is no interactive/out-of-order mode under this execution
model (`run_kind` is always `clean_run_all` on the sidecar side). Writes the
executed notebook back to the same path (the rw bind mount is what actually
persists it into the vault) and exits non-zero if any cell errored, so the
sidecar can tell a clean run from a failed one from the container's own
exit code alone — no notebook-content parsing needed on the sidecar side.

Invoked as: `python /opt/run_notebook.py <notebook_path> <cell_timeout_s> <network_enabled 0|1>`
"""

import subprocess
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def main() -> int:
    notebook_path = Path(sys.argv[1])
    cell_timeout = int(sys.argv[2])
    network_enabled = sys.argv[3] == "1"

    # Dependency installation happens at image-build time by default (D30) —
    # a per-experiment requirements.txt is the one documented exception, and
    # only when this run was itself granted network (belt-and-suspenders
    # with the sidecar's own network_optin gate on the container's network).
    requirements_path = notebook_path.parent / "requirements.txt"
    if network_enabled and requirements_path.exists() and requirements_path.read_text().strip():
        print("[run] installing experiment requirements.txt", flush=True)
        result = subprocess.run(
            ["uv", "pip", "install", "--system", "-r", str(requirements_path)],
            capture_output=True,
            text=True,
        )
        print(result.stdout, flush=True)
        if result.returncode != 0:
            print(result.stderr, flush=True)

    notebook = nbformat.read(notebook_path, as_version=4)
    client = NotebookClient(notebook, timeout=cell_timeout, kernel_name="python3", allow_errors=True)

    had_error = False
    with client.setup_kernel():
        for index, cell in enumerate(notebook.cells):
            if cell.get("cell_type") != "code":
                continue
            print(f"[run] executing cell {index + 1}/{len(notebook.cells)}", flush=True)
            client.execute_cell(cell, index)
            for output in cell.get("outputs", []):
                if output.get("output_type") == "stream":
                    print(output.get("text", ""), end="", flush=True)
                elif output.get("output_type") == "error":
                    had_error = True
                    print("\n".join(output.get("traceback", [])), flush=True)

    nbformat.write(notebook, notebook_path)
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
