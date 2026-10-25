from copy import deepcopy
from hashlib import sha256
import math
import sys

import pytest

from ssc_core import NUMERIC_OFFLINE_V1, SessionRegistry
from ssc_core.program_validation import probe_program


linux = pytest.mark.skipif(sys.platform != "linux", reason="bounded evaluator requires Linux")


def setup_program(catalog, *, axis=False, directed=False, invariant=True, body=None):
    catalog = deepcopy(catalog)
    geometry = next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SceneGeometry")
    program = catalog["constraint_programs"][0]
    if axis:
        if not geometry["axes"]:
            geometry["axes"] = [{"axis_id": "axis-test", "object_id": program["object_id"],
                "origin_keypoint_id": program["keypoint_bindings"][0],
                "unit_direction_xyz": [1, 0, 0], "directed": directed}]
        feature = geometry["axes"][0]
        feature["directed"] = directed
        program["axis_bindings"] = [feature["axis_id"]]
        program["keypoint_bindings"] = []
        program["parameters"] = []
        program["output_unit"] = "dimensionless"
        product = "dot(R_control_tcp[:,0], axis_dirs[0])"
        body = "    return " + (f"abs({product})" if invariant else product) + "\n"
    if body is not None:
        program["source_code"] = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n" + body
        program["source_sha256"] = sha256(program["source_code"].encode()).hexdigest()
    registry = SessionRegistry("session-offline-v1", policy=NUMERIC_OFFLINE_V1)
    for message in catalog["messages"]:
        registry.register_message(message)
    registry.register_record("ConstraintProgram", program, session_id=registry.session_id)
    return registry.resolve_program(
        {"program_id": program["program_id"], "source_sha256": program["source_sha256"]},
        geometry_ref={"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]},
        config_id="config-offline-v1")


def test_motion_enabled_cannot_prepare_or_probe(offline_catalog):
    result = probe_program(setup_program(offline_catalog), motion_disabled=False)
    assert not result.accepted
    assert result.preparation.code == "motion_not_disabled"
    assert not result.probes
    assert result.cleanup.reaped


@pytest.mark.skipif(sys.platform == "linux", reason="non-Linux fail-closed check")
def test_no_unbounded_fallback_on_non_linux(offline_catalog):
    result = probe_program(setup_program(offline_catalog), motion_disabled=True)
    assert not result.accepted
    assert result.preparation.code == "unsupported_platform"
    assert not result.probes


@linux
def test_distance_analytical_oracle_and_replay(offline_catalog):
    resolved = setup_program(offline_catalog)
    first = probe_program(resolved, motion_disabled=True)
    second = probe_program(resolved, motion_disabled=True)
    assert first.accepted and second.accepted, (first.diagnostics, second.diagnostics)
    assert len(first.probes) == 50
    for a, b in zip(first.probes, second.probes):
        expected = math.dist(a.probe.position, resolved.keypoints[0]) - resolved.params[0]
        assert a.outcome.residual == pytest.approx(expected, rel=1e-9, abs=1e-9)
        assert b.outcome.residual == pytest.approx(a.outcome.residual, rel=1e-9, abs=1e-9)
        assert a.outcome.worker_generation == first.preparation.worker_generation
    assert any(p.outcome.residual > 0 for p in first.probes)
    assert first.cleanup.reaped and second.cleanup.reaped


@linux
@pytest.mark.parametrize("directed,invariant,accepted,count", [
    (False, True, True, 98), (False, False, False, None), (True, False, True, 51),
])
def test_axis_sign_contract(offline_catalog, directed, invariant, accepted, count):
    resolved = setup_program(offline_catalog, axis=True, directed=directed, invariant=invariant)
    result = probe_program(resolved, motion_disabled=True)
    assert result.accepted is accepted, result.diagnostics
    if count is not None:
        assert len(result.probes) == count
    else:
        assert any("axis_sign_invariance" in message for message in result.diagnostics)
    assert result.cleanup.reaped


@linux
@pytest.mark.parametrize("body,stage,code", [
    ("    return 1 / 0\n", "evaluation", "division_by_zero"),
    ("    return sqrt(-1)\n", "evaluation", "domain_error"),
    ("    return 1e308 * 1e308\n", "evaluation", "non_finite"),
    ("    return p_tcp\n", "compile", "non_scalar_return"),
    ("    import os\n    return 0\n", "compile", "forbidden_syntax"),
])
def test_probe_failure_retires_worker(offline_catalog, body, stage, code):
    result = probe_program(setup_program(offline_catalog, body=body), motion_disabled=True)
    assert not result.accepted
    failure = result.preparation if stage == "compile" else result.probes[-1].outcome
    assert failure.stage == stage and failure.code == code
    assert failure.residual is None and failure.reaped
    assert result.cleanup.reaped
