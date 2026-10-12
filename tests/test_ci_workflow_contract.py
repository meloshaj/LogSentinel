from __future__ import annotations

from scripts.validate_ci_workflow import (
    REQUIRED_RELEASE_GATE_DEPS,
    REQUIRED_RELEASE_JOBS,
    inspect_workflows,
)


def test_all_workflows_are_pinned_and_least_privilege() -> None:
    payload = inspect_workflows()

    assert payload["status"] == "PASS", payload["errors"]
    assert payload["workflow_count"] == 2
    assert all(len(item["sha"]) == 40 for item in payload["actions"])
    assert all("@sha256:" in item["image"] for item in payload["images"])
    assert payload["permission_writes"] == []
    assert payload["failure_semantics"]["mandatory_continue_on_error"] is False
    assert payload["failure_semantics"]["mandatory_ignored_exit_codes"] is False


def test_release_gate_is_complete_transitive_fail_closed_dag() -> None:
    payload = inspect_workflows()
    dag = payload["dag"]

    assert dag["job_count"] == len(REQUIRED_RELEASE_JOBS)
    assert set(dag["release_gate_direct_dependencies"]) == REQUIRED_RELEASE_GATE_DEPS
    assert (
        set(dag["release_gate_transitive_dependencies"]) == REQUIRED_RELEASE_GATE_DEPS
    )
    assert dag["missing_mandatory_dependencies"] == []
    assert dag["circular_dependency"] is False
    assert dag["optional_bypass_detected"] is False
    assert dag["security_job_result_must_equal_success"] is True
