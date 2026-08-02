# Pinned experiment base image (D30/TRD §2.7) — numpy/pandas/torch/
# scikit-learn/matplotlib preinstalled so a run starts in ~1s without a
# network fetch. Per-experiment deps layer on top via `uv` + the
# experiment's own requirements.txt at run time (image-build-time network,
# never at execution time — D30's "network off by default" applies to the
# running container, not this build).
FROM python:3.11-slim

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir numpy pandas scikit-learn matplotlib uv

# nbclient/nbformat/ipykernel: the *sidecar's* venv already having these
# (Phase 2.1's kernel-transport spike) does nothing for code that runs
# *inside* this container — `run_notebook.py` below is what actually
# executes a run's notebook, in-container, via nbclient under a one-shot
# `docker run` (D30's descope fallback; Phase 2.2 is the first real caller).
RUN pip install --no-cache-dir nbclient nbformat ipykernel && \
    python -m ipykernel install --sys-prefix --name python3

COPY run_notebook.py /opt/run_notebook.py

WORKDIR /workspace
