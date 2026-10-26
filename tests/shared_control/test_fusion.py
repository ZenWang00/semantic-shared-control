"""Analytical support oracles and rejection tests for the installed estimator."""

from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from ssc_core import NUMERIC_OFFLINE_V1, estimate_intent, get_intent_config, to_dict


HELPER = Path(__file__).resolve().parents[2] / "docs/reference/intent_fixtures.py"
FIXTURES = runpy.run_path(str(HELPER))
fixture, CASES = FIXTURES["fixture"], FIXTURES["CASES"]
CONFIG = get_intent_config("intent-linear-v1")


def estimate(value, config=CONFIG, **kwargs):
    return estimate_intent(value, config, weights_id="weights-fixture", policy=NUMERIC_OFFLINE_V1, **kwargs)


def change_current(value, **fields):
    return replace(value, joystick=replace(value.joystick, payload=replace(value.joystick.payload, **fields)))


def change_history(value, items):
    return replace(value, joystick_history=tuple(items), snapshot=replace(
        value.snapshot, joystick_history_refs=tuple(item.meta.message_id for item in items)))


@pytest.mark.parametrize("name,arguments,expected,alpha,disposition", CASES, ids=[case[0] for case in CASES])
def test_analytical_profile_examples(name, arguments, expected, alpha, disposition):
    value = fixture(**arguments)
    config = get_intent_config("intent-linear-alpha0-v1") if arguments.get("alpha_zero") else CONFIG
    before = asdict(value)
    result = estimate(value, config)
    assert result.disposition == disposition, result
    assert tuple(item.weight for item in result.weights.weights) == pytest.approx(expected, abs=1e-12, rel=0)
    assert result.weights.assistance_strength == alpha
    assert result.weights.snapshot_id == value.snapshot.snapshot_id
    assert result.weights.estimator_config_id == config.estimator_config_id
    assert sum(item.weight for item in result.weights.weights) == pytest.approx(1., abs=1e-12, rel=0)
    assert [item.object_id for item in result.weights.weights] == sorted(item.object_id for item in value.bundle.candidates)
    assert asdict(value) == before
    assert estimate(value, config) == result
    with pytest.raises(FrozenInstanceError):
        result.weights.assistance_strength = 1


@pytest.mark.parametrize("field,value", [
    ("alpha_active", -1.), ("alpha_active", 1.1), ("alpha_active", float("nan")),
    ("alpha_active", True), ("history_limit", 33), ("history_window_s", 2.),
    ("score_floor", .5), ("current_coefficient", 1.), ("estimator_config_id", "unknown"),
    ("session_config_id", "other"), ("approach_keypoints", ()),
    ("approach_keypoints", list(CONFIG.approach_keypoints)),
])
def test_changed_or_invalid_configuration_has_no_usable_weights(field, value):
    result = estimate(fixture(), replace(CONFIG, **{field: value}))
    assert result.disposition == "error" and result.weights is None
    assert result.code == "invalid_config"


def test_configuration_lookup_is_immutable_and_explicit():
    assert get_intent_config(CONFIG.estimator_config_id) == CONFIG
    assert get_intent_config(CONFIG.estimator_config_id) is not CONFIG
    with pytest.raises(FrozenInstanceError):
        CONFIG.alpha_active = .1
    with pytest.raises(ValueError, match="unsupported"):
        get_intent_config("missing")
    result = estimate(fixture(alpha_zero=True))
    assert result.code == "config_reference" and result.weights is None
    result = estimate_intent(fixture(), CONFIG, weights_id="w", policy=replace(NUMERIC_OFFLINE_V1, weight_sum_tolerance=1.))
    assert result.code == "invalid_policy" and result.weights is None


@pytest.mark.parametrize("mutation", [
    lambda x: change_current(x, u_xy=(float("nan"), 0)),
    lambda x: change_current(x, u_xy=(float("inf"), 0)),
    lambda x: change_current(x, u_xy=(True, 0)),
    lambda x: change_current(x, u_xy=(1.1, 0)),
    lambda x: change_current(x, enabled=1),
    lambda x: replace(x, snapshot=replace(x.snapshot, joystick_ref="missing")),
    lambda x: replace(x, snapshot=replace(x.snapshot, robot_state_ref="missing")),
    lambda x: replace(x, snapshot=replace(x.snapshot, semantic_id="missing")),
    lambda x: replace(x, snapshot=replace(x.snapshot, geometry_ref=replace(x.snapshot.geometry_ref, revision=2))),
    lambda x: replace(x, snapshot=replace(x.snapshot, task_ref=replace(x.snapshot.task_ref, revision=2))),
    lambda x: replace(x, snapshot=replace(x.snapshot, program_refs=x.snapshot.program_refs * 2)),
    lambda x: replace(x, snapshot=replace(x.snapshot, execution_binding_refs=x.snapshot.execution_binding_refs * 2)),
    lambda x: replace(x, bundle=replace(x.bundle, session_id="foreign")),
    lambda x: replace(x, bundle=replace(x.bundle, candidates=())),
    lambda x: replace(x, bundle=replace(x.bundle, candidates=x.bundle.candidates * 2)),
    lambda x: replace(x, bundle=replace(x.bundle, semantic=replace(x.bundle.semantic, object_relevance=()))),
    lambda x: replace(x, bundle=replace(x.bundle, semantic=replace(x.bundle.semantic, object_relevance=(
        replace(x.bundle.semantic.object_relevance[0], task_relevance=-1), x.bundle.semantic.object_relevance[1])))),
    lambda x: replace(x, bundle=replace(x.bundle, programs=x.bundle.programs[:1])),
    lambda x: replace(x, bundle=replace(x.bundle, execution_bindings=())),
    lambda x: replace(x, bundle=replace(x.bundle, receipts=())),
    lambda x: replace(x, bundle=replace(x.bundle, programs=(replace(x.bundle.programs[0], keypoints=((0., 0., 0.),)), x.bundle.programs[1]))),
    lambda x: replace(x, bundle=replace(x.bundle, programs=(replace(x.bundle.programs[0], program=replace(x.bundle.programs[0].program, source_code="changed")), x.bundle.programs[1]))),
    lambda x: replace(x, bundle=replace(x.bundle, receipts=(replace(x.bundle.receipts[0], status="rejected"), x.bundle.receipts[1]))),
    lambda x: replace(x, bundle=replace(x.bundle, execution_bindings=(replace(x.bundle.execution_bindings[0], config_id="other"), x.bundle.execution_bindings[1]))),
    lambda x: replace(x, bundle=replace(x.bundle, geometry=replace(x.bundle.geometry, frame_id="wrong"))),
    lambda x: replace(x, robot_state=replace(x.robot_state, payload=replace(x.robot_state.payload, tcp_frame="wrong"))),
])
def test_invalid_records_and_snapshot_references_never_return_weights(mutation):
    for value in (fixture(), fixture(current=(0, 0)), fixture(enabled=False)):
        outcome = estimate(mutation(value))
        assert outcome.disposition == "error", outcome
        assert outcome.weights is None and outcome.diagnostic


@pytest.mark.parametrize("mutation", [
    lambda h: replace(h, payload=replace(h.payload, mapping_id="other")),
    lambda h: replace(h, payload=replace(h.payload, sampled_at=replace(h.payload.sampled_at, clock_id="other"))),
    lambda h: replace(h, payload=replace(h.payload, sampled_at=replace(h.payload.sampled_at, sec=11))),
    lambda h: replace(h, payload=replace(h.payload, sampled_at=replace(h.payload.sampled_at, sec=8))),
    lambda h: replace(h, payload=replace(h.payload, u_xy=(float("nan"), 0))),
    lambda h: replace(h, meta=replace(h.meta, sequence=100)),
    lambda h: replace(h, meta=replace(h.meta, session_id="other")),
    lambda h: replace(h, meta=replace(h.meta, source_id="other")),
    lambda h: replace(h, meta=replace(h.meta, message_id="current-input")),
])
def test_invalid_history_cannot_be_ignored_even_when_disabled(mutation):
    value = fixture(enabled=False, history=(((1, 0), True),))
    outcome = estimate(change_history(value, (mutation(value.joystick_history[0]),)))
    assert outcome.disposition == "error" and outcome.weights is None


def test_history_order_duplicate_limit_and_exact_boundary():
    value = fixture(history=(((1, 0), True), ((0, 1), True)))
    for history in (value.joystick_history[::-1], value.joystick_history * 2, (value.joystick_history[0],)*33):
        outcome = estimate(change_history(value, history))
        assert outcome.disposition == "error" and outcome.weights is None
    assert estimate(fixture(history=(((1, 0), True),)*32)).disposition == "active"
    history = value.joystick_history[0]
    for sec, ns, valid in ((9, 0, True), (8, 999999999, False), (10, 0, True), (10, 1, False)):
        altered = replace(history, payload=replace(history.payload, sampled_at=replace(history.payload.sampled_at, sec=sec, nanosec=ns)))
        outcome = estimate(change_history(value, (altered,)))
        assert (outcome.disposition == "active") == valid
    equal_time = replace(value.joystick_history[1], payload=replace(value.joystick_history[1].payload,
                         sampled_at=value.joystick_history[0].payload.sampled_at))
    assert estimate(change_history(value, (history, equal_time))).disposition == "active"


def test_nonfinite_intermediate_is_rejected():
    value = fixture(positions=((1e308, 0, .3), (.39, 0, .3)))
    state = replace(value.robot_state.payload, tcp_pose=replace(value.robot_state.payload.tcp_pose, position_xyz=(-1e308, 0, .3)))
    outcome = estimate(replace(value, robot_state=replace(value.robot_state, payload=state)))
    assert outcome.code == "nonfinite_computation" and outcome.weights is None


def test_subnormal_diagonal_current_and_history_preserve_direction():
    positions = ((.41, .01, .3), (.39, -.01, .3))
    regular = estimate(fixture(current=(.5, .5), positions=positions))
    tiny = estimate(fixture(current=(5e-324, 5e-324), positions=positions))
    assert [w.weight for w in tiny.weights.weights] == pytest.approx([w.weight for w in regular.weights.weights], abs=1e-12, rel=0)
    regular = estimate(fixture(history=(((.5, .5), True),), positions=positions))
    tiny = estimate(fixture(history=(((5e-324, 5e-324), True),), positions=positions))
    assert [w.weight for w in tiny.weights.weights] == pytest.approx([w.weight for w in regular.weights.weights], abs=1e-12, rel=0)


def test_approach_point_at_epsilon_is_neutral_and_missing_mapping_fails():
    value = fixture(positions=((.4, 1e-12, .3), (.4, -1e-12, .3)), current=(0, 1))
    assert [w.weight for w in estimate(value).weights.weights] == [.5, .5]
    # An otherwise valid geometry change cannot silently retarget a fixed mapping.
    value = fixture()
    points = (replace(value.bundle.geometry.keypoints[0], keypoint_id="different"), value.bundle.geometry.keypoints[1])
    outcome = estimate(replace(value, bundle=replace(value.bundle, geometry=replace(value.bundle.geometry, keypoints=points))))
    assert outcome.code == "approach_mapping" and outcome.weights is None


def test_replay_in_a_fresh_process(tmp_path):
    code = '''
import json, runpy, sys
from ssc_core import estimate_intent, get_intent_config, NUMERIC_OFFLINE_V1, to_dict
f = runpy.run_path(sys.argv[1])["fixture"]
r = estimate_intent(f(current=(-1, 0), history=(((1, 0), True),)), get_intent_config("intent-linear-v1"), weights_id="weights-fixture", policy=NUMERIC_OFFLINE_V1)
print(json.dumps(to_dict(r.weights), sort_keys=True, allow_nan=False))
'''
    actual = json.loads(subprocess.run([sys.executable, "-I", "-c", code, str(HELPER)], cwd=tmp_path,
                                      check=True, capture_output=True, text=True).stdout)
    expected = estimate(fixture(current=(-1, 0), history=(((1, 0), True),)))
    assert actual == to_dict(expected.weights)
