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

# Phase 2.4: un-descoping D30's original interactive-kernel path over
# Jupyter's own HTTP/WebSocket protocol on a published TCP port (proven
# working in this project's own spikes) rather than raw jupyter_client/ZMQ.
# `notebook` (Jupyter Notebook 7) brings jupyter-server transitively.
RUN pip install --no-cache-dir notebook

COPY run_notebook.py /opt/run_notebook.py
COPY jupyter_server_config.py /opt/jupyter_server_config.py

# JupyterLab's own default autosave interval is 120s (docmanager-extension's
# `autosaveInterval` setting) — with the live notebook server torn down as
# soon as its panel closes, that's a real window to lose recent edits in
# (confirmed live: edits made and not yet autosaved were gone from the vault
# file after a normal stop). This is the documented, supported override
# mechanism (not a hack) for shortening it; 3s bounds that window to
# something that matches how the panel is actually used.
COPY jupyterlab_overrides.json /usr/local/share/jupyter/lab/settings/overrides.json

WORKDIR /workspace
