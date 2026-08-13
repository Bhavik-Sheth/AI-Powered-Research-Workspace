"""Harness eval scaffolding (HarnessPlan H1, §3.10) — deterministic
scenarios run in CI against a scripted stub LLM (`stub_llm.py`); live
scenarios (`pytest -m live`) run against a real small-model endpoint and
are not part of the default suite. Neither tier uses an LLM judge —
assertions are structural (the right tool was called, every citation
validated, context stayed under budget), per HarnessPlan §3.10.
"""
