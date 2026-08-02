# Config for the per-experiment live notebook server (Phase 2.4).
#
# Baked into the image and passed via `--config=/opt/jupyter_server_config.py`
# on every notebook-server container. Every setting here exists to let the
# real Jupyter web UI render and run cells inside an <iframe> embedded from a
# *different* origin than the notebook server itself (the app's own dev
# server, or a `file://`-loaded packaged renderer) — verified concretely by
# spike, not assumed:
#
# - Jupyter's default `frame-ancestors 'self'` CSP blocks being framed by any
#   other origin outright; a `file://` embedder has an opaque origin with no
#   CSP keyword that reliably allow-lists it in modern Chromium. Disabling
#   the header entirely was the only approach that rendered from both a
#   `file://` page and an `http://localhost:<port>` dev-server page in
#   testing.
# - Jupyter's XSRF check inspects the Referer header against its own origin;
#   an iframed, cross-origin request fails it on any state-changing call
#   (saving a cell, running a cell) unless disabled.
# - Token-based auth only protects the *initial* HTTP page load — the
#   notebook UI's own kernel WebSocket connections do not re-attach the
#   token, and rely on a session cookie instead. That cookie does not
#   reliably reach the server from a cross-origin/opaque-origin iframe
#   (browsers restrict this), so with a token still required, every kernel
#   WebSocket handshake was rejected with 403 in testing even though the page
#   itself loaded fine. Disabling the token (and password auth) was the only
#   way that produced a working kernel connection end to end.
#
# The actual security boundary for this server is not any of Jupyter's own
# auth/CSP machinery — it's that the container's port is published to
# 127.0.0.1 only, on a freshly-chosen ephemeral port, torn down when the
# experiment's live-notebook panel closes (D2's single-local-user threat
# model). This trade-off is deliberate, not an oversight.

c = get_config()  # noqa: F821  (injected by `jupyter --config=`)

c.ServerApp.ip = "0.0.0.0"  # binds inside the container; the host only ever reaches this via the published loopback port
c.ServerApp.open_browser = False
c.ServerApp.tornado_settings = {"headers": {"Content-Security-Policy": ""}}
c.ServerApp.disable_check_xsrf = True
c.ServerApp.allow_origin = "*"
c.IdentityProvider.token = ""
c.ServerApp.password_required = False
