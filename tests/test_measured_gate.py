"""D29 `measured` gate suite (Rules.md) — pure function over a fixture run
record, no network/Postgres/Docker/LLM.

`experiments.is_measured_eligible` is the pre-DB predicate `record_metric`
fast-fails against before a `source: measured` write; the actual
enforcement point is still `experiment_metrics`'s own DB CHECK
(`source <> 'measured' OR run_id IS NOT NULL`) plus
`experiment_runs.run_kind`'s CHECK — this suite never touches Postgres, it
only tests the pure Python gate that mirrors that rule (MODULES.md).

Only a clean `run_kind='clean_run_all'` run that exited 0 and carries all
four provenance fields (run id, image digest, `reqs_hash`, `notebook_hash`,
a timestamp) is eligible. Interactive, out-of-order, non-zero-exit, or
missing-provenance runs must never be eligible.
"""

from datetime import datetime, timezone

from experiments import is_measured_eligible
from experiments.models import ExperimentRun

_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_run(**overrides) -> ExperimentRun:
    """A clean, eligible run record — every test overrides exactly the one
    field it's exercising, per Rules.md's fixture-based mocking policy.

    Built via `model_construct` (skips Pydantic validation) rather than the
    normal constructor: a real `experiment_runs` row can never have a NULL
    `approved_at` (DB NOT NULL, D31's consent gate at rest), but this suite
    must still assert the *predicate* refuses such a record if one somehow
    reached it — `model_construct` is the standard Pydantic escape hatch for
    representing exactly that kind of malformed fixture without the
    constructor rejecting it first.
    """
    fields = dict(
        id="11111111-1111-1111-1111-111111111111",
        experiment_id="22222222-2222-2222-2222-222222222222",
        started_at=_NOW,
        finished_at=_NOW,
        exit_code=0,
        image="sha256:abcdef0123456789",
        reqs_hash="reqs-hash-abc",
        notebook_hash="notebook-hash-abc",
        stdout_ref="projects/p/experiments/e/runs/11111111-1111-1111-1111-111111111111/stdout.log",
        artifacts=[],
        run_kind="clean_run_all",
        network_enabled=False,
        gpu_enabled=False,
        approved_at=_NOW,
    )
    fields.update(overrides)
    return ExperimentRun.model_construct(**fields)


class TestCleanRunAllExitZeroIsEligible:
    def test_a_clean_run_all_with_full_provenance_is_eligible(self):
        assert is_measured_eligible(_make_run()) is True

    def test_eligibility_does_not_depend_on_finished_at(self):
        # finished_at is not one of the four provenance fields the gate
        # checks (started_at/approved_at cover the timestamp requirement).
        assert is_measured_eligible(_make_run(finished_at=None)) is True


class TestInteractiveRunsAreNeverEligible:
    def test_interactive_run_kind_is_never_eligible(self):
        assert is_measured_eligible(_make_run(run_kind="interactive")) is False

    def test_interactive_run_kind_is_never_eligible_even_with_exit_zero_and_full_provenance(self):
        run = _make_run(run_kind="interactive", exit_code=0)
        assert is_measured_eligible(run) is False


class TestNonZeroOrMissingExitCodeIsNeverEligible:
    def test_non_zero_exit_code_is_never_eligible(self):
        assert is_measured_eligible(_make_run(exit_code=1)) is False

    def test_missing_exit_code_is_never_eligible(self):
        # None while a run is still in flight (or after a cancel/kill).
        assert is_measured_eligible(_make_run(exit_code=None)) is False


class TestMissingProvenanceFieldIsNeverEligible:
    def test_missing_image_digest_is_never_eligible(self):
        assert is_measured_eligible(_make_run(image="")) is False

    def test_missing_reqs_hash_is_never_eligible(self):
        assert is_measured_eligible(_make_run(reqs_hash="")) is False

    def test_missing_notebook_hash_is_never_eligible(self):
        assert is_measured_eligible(_make_run(notebook_hash="")) is False

    def test_missing_approved_at_timestamp_is_never_eligible(self):
        assert is_measured_eligible(_make_run(approved_at=None)) is False
