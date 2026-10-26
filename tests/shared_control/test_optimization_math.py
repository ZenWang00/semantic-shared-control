"""Hand-checkable solver-slsqp-v1 objective/oracle checks without processes.

These call the private numerical core with analytical residual callbacks. The
real supervised path (interpreter workers, deadlines, teardown) is covered in
test_optimization.py; these establish the formula and failure classification.
"""
import math
import time

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from ssc_core import _solver_math as m
from ssc_core.optimization import _relative_rotvec, basic_step_margins
from ssc_core.records import Pose

L, A = .01, .1
ORIGIN = (.4, 0., .3)


def problem(u=(1., 0.), alpha=.5, terms=(), prev=(0.,) * 6, **extra):
    data = {"p_tcp": list(ORIGIN), "q_tcp": [0., 0., 0., 1.], "L_m": L, "A_rad": A,
            "xy_gain": .05 * .2 / L, "u_xy": list(u), "alpha": alpha, "smoothing": .1,
            "x_prev": list(prev[:3]), "r_prev": list(prev[3:]), "terms": list(terms),
            "hard_margin_tolerance": 1e-8}
    data.update(extra)
    return data


def distance(program_id, target, tolerance=0., scale=L, weight=1.):
    return {"program_id": program_id, "kind": "distance", "target": target, "tolerance": tolerance,
            "scale": scale, "object_weight": weight, "binding_weight": 1.}


def evaluate(term, p, rotation):
    if term["kind"] == "distance":
        return math.dist(p, term["target"]) - term["tolerance"]
    approach = [rotation[0][0], rotation[1][0], rotation[2][0]]
    cosine = max(-1., min(1., sum(a * b for a, b in zip(approach, term["axis"]))))
    return math.acos(cosine) - term["tolerance"]


def free(position, quaternion):
    return ["free"], [1.0]


def solve(data, host=free, deadline_s=5.):
    return m.solve_problem(data, evaluate, host, deadline_ns=time.monotonic_ns() + int(deadline_s * 1e9))


def delta(report):
    return np.asarray(report["position_xyz"]) - ORIGIN


def assert_oracle(report, translation, rotation=(0., 0., 0.)):
    assert np.linalg.norm(delta(report) - translation) <= 1e-6
    angle = (Rotation.from_quat(report["quaternion_xyzw"]) * Rotation.from_rotvec(rotation).inv()).magnitude()
    assert angle <= 1e-5


def test_joystick_only_oracle():
    report = solve(problem(alpha=0.))
    assert_oracle(report, (10 * L / 11, 0, 0))
    assert report["evaluations"] == []


def test_positive_residual_approach_moves_with_unsatisfied_relation():
    report = solve(problem(terms=[distance("bottle", (ORIGIN[0] + 2 * L, 0, .3), tolerance=L)]))
    assert_oracle(report, (15 * L / 16, 0, 0))
    g = report["evaluations"][0]["raw_g"]
    assert g == pytest.approx(L / 16, abs=1e-6) and g > 1e-9
    assert (max(0., g) / L) ** 2 == pytest.approx(1 / 256, abs=1e-4)


def test_axis_alignment_rotates_about_control_z_and_keeps_user_translation():
    axis = {"program_id": "axis", "kind": "axis", "axis": (math.cos(A), math.sin(A), 0.), "tolerance": 0.,
            "scale": A, "object_weight": 1., "binding_weight": 1.}
    report = solve(problem(terms=[axis]))
    assert_oracle(report, (10 * L / 11, 0, 0), (0, 0, 5 * A / 6))
    assert report["evaluations"][0]["raw_g"] == pytest.approx(A / 6, abs=1e-5)


def test_reversal_with_previous_reference_responds_immediately():
    terms = [distance("bottle", (ORIGIN[0] + L, 0, .3), weight=5 / 16),
             distance("cup", (ORIGIN[0] - L, 0, .3), weight=11 / 16)]
    report = solve(problem(u=(-1., 0.), terms=terms, prev=(1., 0, 0, 0, 0, 0)))
    assert_oracle(report, (-87 * L / 128, 0, 0))
    assert delta(report)[0] < 0  # no opposing step sustained by history or smoothing


def test_alpha_zero_removes_semantic_influence_even_with_strong_targets():
    terms = [distance("bottle", (ORIGIN[0], .5, .3), weight=1.)]
    with_terms = solve(problem(alpha=0., terms=terms))
    assert_oracle(with_terms, (10 * L / 11, 0, 0))
    assert with_terms["terms"]["assistance"] == 0.
    assert with_terms["evaluations"][0]["raw_g"] > 0  # evaluated, just not influencing


def test_objective_terms_are_hand_checkable():
    data = problem(u=(.6, .8), alpha=.5, terms=[distance("a", (0, 0, 0), scale=.02, weight=.25),
                                               distance("b", (0, 0, 0), scale=.01, weight=.75)],
                   prev=(.1, 0, 0, 0, 0, .2))
    data["terms"][1]["binding_weight"] = 2.
    z = np.array([.5, .5, 0, 0, 0, 0])
    tracking, smoothing, assistance = m.objective_terms(data, z, [.01, -.01])
    assert tracking == pytest.approx((.5 - .6) ** 2 + (.5 - .8) ** 2)
    assert smoothing == pytest.approx(.1 * ((.5 - .1) ** 2 + .5 ** 2 + .2 ** 2))
    assert assistance == pytest.approx(.5 * (.25 * (.01 / .02) ** 2 + .75 * 2 * 0.))


def test_numpy_and_standard_library_margins_agree():
    data = problem(u=(.3, -.4))
    x, r = np.array([.2, -.1, .05]), np.array([-.3, .1, .2])
    position, rotation = m.candidate_pose(data, np.concatenate((x, r)))
    quaternion = m.canonical_quaternion(rotation)
    assert quaternion[3] >= 0
    target = Pose(tuple(position.tolist()), tuple(quaternion.tolist()))
    measured = Pose(ORIGIN, (0., 0., 0., 1.))
    host = basic_step_margins(target, measured, (.3, -.4), step_m=L, step_rad=A)
    numpy = m.basic_margins(data, *m.emitted_increment(data, position, quaternion))
    assert host.keys() == numpy.keys()
    for name in host:
        assert host[name] == pytest.approx(numpy[name], abs=1e-12)
    assert host["input_half_space"] == pytest.approx((.3 * .2 + -.4 * -.1) / .5)


@pytest.mark.parametrize("rotvec", [(0, 0, 0), (.1, -.2, .05), (0, 0, math.pi - 1e-6), (1e-9, 0, 0)])
def test_standard_library_log_matches_scipy(rotvec):
    source = Rotation.from_rotvec((.3, .2, -.1))
    target = Rotation.from_rotvec(rotvec) * source
    observed = _relative_rotvec(target.as_quat().tolist(), (-source.as_quat()).tolist())
    assert np.allclose(observed, rotvec, atol=1e-9)


def test_half_space_forbids_opposing_next_step_against_attractive_target():
    terms = [distance("behind", (ORIGIN[0] - 5 * L, 0, .3), weight=1.)]
    report = solve(problem(u=(1., 0.), alpha=1., terms=terms))
    assert delta(report)[0] >= -1e-10
    assert dict(report["basic_margins"])["input_half_space"] >= -1e-8


def test_host_margin_limits_motion_and_names_are_reported():
    def wall(position, quaternion):
        return ["wall"], [(ORIGIN[0] + .004 - position[0]) / .2]
    report = solve(problem(), host=wall)
    assert delta(report)[0] == pytest.approx(.004, abs=1e-7)
    assert dict(report["host_margins"])["wall"] >= -1e-8


def test_failures_are_classified_and_never_partial():
    def raising(term, p, rotation):
        raise m.SolveFailure("error", "evaluator_domain", "boom", evaluation_valid=False)
    with pytest.raises(m.SolveFailure) as error:
        m.solve_problem(problem(terms=[distance("a", ORIGIN)]), raising, free,
                        deadline_ns=time.monotonic_ns() + 10**9)
    assert (error.value.status, error.value.evaluation_valid) == ("error", False)

    with pytest.raises(m.SolveFailure) as error:
        m.solve_problem(problem(), evaluate, free, deadline_ns=time.monotonic_ns() - 1)
    assert error.value.status == "timeout"

    def huge(term, p, rotation):
        return 1e308  # finite residual whose normalized squared hinge overflows
    with pytest.raises(m.SolveFailure) as error:
        m.solve_problem(problem(terms=[distance("a", ORIGIN, scale=1e-300)]), huge, free,
                        deadline_ns=time.monotonic_ns() + 10**9)
    assert (error.value.code, error.value.evaluation_valid) == ("nonfinite_objective", False)

    calls = {"n": 0}
    def changing(position, quaternion):
        calls["n"] += 1
        return [f"name-{calls['n']}"], [1.]
    with pytest.raises(m.SolveFailure) as error:
        solve(problem(), host=changing)
    assert error.value.code == "host_margin_names"


def test_infeasible_hard_constraints_produce_infeasible():
    def outside(position, quaternion):  # measured TCP already beyond a wall it may not cross back over
        return ["wall"], [(ORIGIN[0] - .005 - position[0]) / .2]
    with pytest.raises(m.SolveFailure) as error:
        solve(problem(u=(1., 0.)), host=outside)
    assert error.value.status == "infeasible"


def test_iteration_exhaustion_is_a_budget_timeout(monkeypatch):
    monkeypatch.setitem(m.SLSQP_OPTIONS, "maxiter", 1)
    terms = [distance("bottle", (ORIGIN[0] + L, 0, .3), weight=5 / 16),
             distance("cup", (ORIGIN[0] - L, 0, .3), weight=11 / 16)]
    with pytest.raises(m.SolveFailure) as error:
        solve(problem(u=(-1., 0.), terms=terms, prev=(1., 0, 0, 0, 0, 0)))
    assert (error.value.status, error.value.code) == ("timeout", "iteration_limit")


def test_identical_inputs_replay_identically():
    terms = [distance("bottle", (ORIGIN[0] + 2 * L, 0, .3), tolerance=L)]
    first, second = solve(problem(terms=terms)), solve(problem(terms=terms))
    assert first["position_xyz"] == second["position_xyz"]
    assert first["evaluations"] == second["evaluations"]
