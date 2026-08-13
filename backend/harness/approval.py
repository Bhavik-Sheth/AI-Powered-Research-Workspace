"""Approval gate for `tier="confirm"` tool calls (HarnessPlan H7, §3.9).

Owns exactly one decision: how a pending approval request is tracked and
resolved between the two places that must agree on it — `loop.py`, which
registers a request before yielding `ApprovalRequestEvent` and then awaits
its outcome, and `ws/__init__.py`'s `handle_message`, which resolves one
when an `ApprovalResponseEvent` arrives over the socket. This mirrors
`loop.py`'s own `_in_flight` idiom: a module-level dict keyed by an opaque
id, a reservation function, and a resolution function — no class, no
persistence, because a `request_id` that doesn't survive a sidecar restart
is fine (the turn it belongs to couldn't survive one either).

A `request_id` is a fresh `uuid4` per call (`loop.py`), so it is never
reused across turns or sessions. Resolving one with no pending future —
already timed out, already resolved, or a stale/duplicate response — is a
silent no-op, not an error: the human's answer arrived either too late or
twice, and the turn has already moved on either way.
"""

import asyncio

__all__ = ["register", "resolve", "discard"]

_pending: dict[str, asyncio.Future[bool]] = {}


def register(request_id: str) -> asyncio.Future[bool]:
    """Creates and returns the `Future` `loop.py` races against
    `cancel_flag` and a timeout for this `request_id`'s outcome."""
    future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
    _pending[request_id] = future
    return future


def resolve(request_id: str, approved: bool) -> None:
    """Called from `ws/__init__.py`'s `handle_message` on an
    `ApprovalResponseEvent`. A no-op if `request_id` has no pending future
    (already timed out, already resolved, or a stale/duplicate response)."""
    future = _pending.get(request_id)
    if future is not None and not future.done():
        future.set_result(approved)


def discard(request_id: str) -> None:
    """Removes a pending future once `loop.py`'s race is over, win or lose
    — called from that race's own `finally` so a request that timed out or
    was interrupted doesn't sit in the registry forever waiting for a
    response that may never come."""
    _pending.pop(request_id, None)
