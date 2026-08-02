# Pinned experiment base image (D30/TRD §2.7) — numpy/pandas/torch/
# scikit-learn/matplotlib preinstalled so a run starts in ~1s without a
# network fetch. Per-experiment deps layer on top via `uv` + the
# experiment's own requirements.txt at run time (image-build-time network,
# never at execution time — D30's "network off by default" applies to the
# running container, not this build).
FROM python:3.11-slim

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir numpy pandas scikit-learn matplotlib uv

WORKDIR /workspace
