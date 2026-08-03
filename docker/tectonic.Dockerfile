# Tectonic-in-Docker escape hatch for final LaTeX compiles needing full
# package coverage (D34/TRD §1.3) — SwiftLaTeX WASM in the renderer is the
# default; this image only runs when a user explicitly asks for a Tectonic
# compile. Tectonic ships as a single self-contained executable (no TeX Live
# tree to install), so the image just needs the binary plus a place to run
# it (network is enabled at build time only, per D30 — the running container
# still gets `--network none` since Tectonic fetches its own bundled format
# files from within the binary on first run and then caches them).
# trixie (glibc 2.40+), not bookworm (glibc 2.36) — the prebuilt tectonic
# binary below requires GLIBC_2.39.
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libgraphite2-3 libharfbuzz0b libfontconfig1 libicu76 libssl3 zlib1g && \
    cd /usr/local/bin && \
    curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh && \
    chmod +x /usr/local/bin/tectonic && \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
ENTRYPOINT ["tectonic"]
