# Tectonic-in-Docker: the manuscript preview's compile engine (Phase 4.1 —
# supersedes D34/TRD §1.3's SwiftLaTeX-WASM-in-the-renderer default. That
# default never worked as provisioned: the upstream SwiftLaTeX release ships
# no `swiftlatexpdftex.fmt`, and even a working one would still resolve
# every `\usepackage` from SwiftLaTeX's hosted texlive endpoint at compile
# time — a live third-party network dependency, which a 2026-08-04 decision
# rules out. Tectonic is used for every compile now, not just the final
# full-package one, but the container it runs in stays exactly what D34
# always intended as the escape hatch — see backend/writing/tectonic.py for
# the invocation and the consent-gate reasoning.
#
# The TeX Live bundle Tectonic needs to resolve packages is baked into the
# image at build time (network allowed here, per D30 — the same pattern
# already used for the `tectonic` binary itself below) as a plain local
# file. The running container gets `--network none`: with the bundle
# already on disk and `--only-cached` set on every compile, there is no
# code path left that reaches out to a host at compile time.
#
# This bundle is a real, non-trivial binary blob (~2.8 GiB downloaded here,
# TeX Live 2021 via Tectonic's own default bundle mirror) baked into the
# image layer, not committed to git — flagged in the Phase 4.1 report as
# the "new dependency" Rules.md asks to call out. It is the full default
# bundle, not a hand-picked subset, because trimming it to "the packages
# this app's documents use" would need reverse-engineering Tectonic's own
# package graph with no official tool for the job; the size cost buys
# offline compiles for any standard LaTeX document, not just a bounded set.
#
# trixie (glibc 2.40+), not bookworm (glibc 2.36) — the prebuilt tectonic
# binary below requires GLIBC_2.39.
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libgraphite2-3 libharfbuzz0b libfontconfig1 libicu76 libssl3 zlib1g && \
    cd /usr/local/bin && \
    curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh && \
    chmod +x /usr/local/bin/tectonic && \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Baked in at build time so the running container never needs the network
# (see header comment) — Tectonic's own default bundle, TeX Live 2021.
RUN mkdir -p /opt/tectonic && \
    apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    curl --proto '=https' --tlsv1.2 -fsSL -o /opt/tectonic/bundle.tar \
        https://relay.fullyjustified.net/default_bundle_v32.tar && \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Tectonic's local `-b`/`--bundle` loader only recognises a directory, a
# `.zip`, or a `.ttb` file (its bundle-detection code keys off the path's
# extension) — a bare `.tar`, which is what the mirror above actually
# serves, is rejected with "doesn't specify a valid bundle". Extracted to a
# plain directory it works as a `DirBundle`. This is deliberately its own
# `RUN`, after the download rather than folded into it: the tar and its
# extracted copy briefly coexist in this layer (removed by its own end, so
# neither survives into the final image), but keeping the slow network
# fetch in its own unchanged layer means a Dockerfile edit down here never
# forces a re-download on the next build.
RUN mkdir -p /opt/tectonic/bundle && \
    tar -xf /opt/tectonic/bundle.tar -C /opt/tectonic/bundle && \
    rm /opt/tectonic/bundle.tar

WORKDIR /workspace
ENTRYPOINT ["tectonic"]
