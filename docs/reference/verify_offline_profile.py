#!/usr/bin/env python3
"""Check proposed offline-profile mathematics against analytic one-step oracles.

Run: python3 -B docs/reference/verify_offline_profile.py

This standalone design-evidence script does not import or implement a shared-
control component, controller, lifecycle, isolation boundary, or command gate.
It writes no files and makes no network requests. Its solver checks are numerical
design checks, not a real-time benchmark or robot validation.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from collections.abc import Callable, Sequence

import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation


L = 0.01  # metres per normalized translational increment
A = 0.1  # radians per normalized rotational increment
POSITION_ATOL_M = 1e-6
ANGLE_ATOL_RAD = 1e-5
WEIGHT_ATOL = 1e-12
FEASIBILITY_ATOL = 1e-8  # dimensionless solver post-check tolerance
OPTIONS = {"eps": 1e-6, "ftol": 1e-9, "maxiter": 100, "disp": False}

# A candidate is (object_id, XY target offset from current TCP, relevance).
Candidate = tuple[str, tuple[float, float], float]
Penalty = Callable[[np.ndarray], float]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def report(record: dict[str, object]) -> None:
    print(json.dumps(record, sort_keys=True, allow_nan=False), flush=True)


def direction(xy: Sequence[float]) -> tuple[float, float]:
    length = math.hypot(*xy)
    if length == 0:
        return (0.0, 0.0)
    return (float(xy[0] / length), float(xy[1] / length))


def reference_support(
    candidates: Sequence[Candidate],
    current: tuple[float, float],
    history: Sequence[tuple[float, float]],
) -> dict[str, float]:
    """Evaluate only the proposed algebra, using already eligible history.

    Source validation, time-window selection and enable/epoch handling belong to
    future components. This helper neither performs nor claims those checks.
    """
    if not candidates:
        raise ValueError("empty candidate set has no normalized support")
    if len({candidate[0] for candidate in candidates}) != len(candidates):
        raise ValueError("candidate identities must be unique")
    if math.hypot(*current) == 0:
        raise ValueError("neutral input has no active current-direction solve")
    current_dir = direction(current)
    history_dirs = [direction(sample) for sample in history if math.hypot(*sample) > 0]
    history_mean = (
        tuple(math.fsum(sample[k] for sample in history_dirs) / len(history_dirs)
              for k in range(2))
        if history_dirs else (0.0, 0.0)
    )
    raw: dict[str, float] = {}
    for object_id, offset, relevance in candidates:
        if not math.isfinite(relevance) or not 0 <= relevance <= 1:
            raise ValueError("relevance must be finite and in [0, 1]")
        target_dir = (0.0, 0.0) if math.hypot(*offset) <= 1e-12 else direction(offset)
        current_alignment = math.fsum(current_dir[k] * target_dir[k] for k in range(2))
        history_alignment = math.fsum(history_mean[k] * target_dir[k] for k in range(2))
        raw[object_id] = (
            0.25 + relevance
            + 2.0 * (1.0 + current_alignment) / 2.0
            + 0.5 * (1.0 + history_alignment) / 2.0
        )
    total = math.fsum(raw.values())
    return {object_id: value / total for object_id, value in raw.items()}


def verify_support() -> dict[str, float]:
    candidates: list[Candidate] = [
        ("left", (-L, 0.0), 0.5),
        ("right", (L, 0.0), 0.5),
    ]
    expected = {"left": 11 / 16, "right": 5 / 16}
    observed = reference_support(candidates, (-1.0, 0.0), [(1.0, 0.0)])
    for object_id in expected:
        check(abs(observed[object_id] - expected[object_id]) <= WEIGHT_ATOL,
              f"reversal support mismatch for {object_id}")
    check(abs(math.fsum(observed.values()) - 1) <= WEIGHT_ATOL,
          "support does not sum to one")
    report({"check": "reversal_support", "expected": expected,
            "observed": observed, "pass": True})

    empty_history = reference_support(candidates, (1.0, 0.0), [])
    expected_empty = {"left": 1 / 4, "right": 3 / 4}
    for object_id in expected_empty:
        check(abs(empty_history[object_id] - expected_empty[object_id]) <= WEIGHT_ATOL,
              f"empty-history support mismatch for {object_id}")
    report({"check": "empty_history", "expected": expected_empty,
            "observed": empty_history, "pass": True})

    reordered = reference_support(list(reversed(candidates)), (-1.0, 0.0), [(1.0, 0.0)])
    for object_id in observed:
        check(abs(reordered[object_id] - observed[object_id]) <= WEIGHT_ATOL,
              "candidate order changed identity-based support")
    report({"check": "candidate_reorder", "expected": observed,
            "observed": reordered, "pass": True})

    # Adversarial valid prior and old history cannot defeat a fully reversed
    # current direction for these two opposite targets: 2 > 1 + 0.5.
    adverse = reference_support(
        [("left", (-L, 0.0), 0.0), ("right", (L, 0.0), 1.0)],
        (-1.0, 0.0), [(1.0, 0.0)],
    )
    expected_adverse = {"left": 9 / 16, "right": 7 / 16}
    for object_id in adverse:
        check(abs(adverse[object_id] - expected_adverse[object_id]) <= WEIGHT_ATOL,
              "current-direction dominance oracle failed")
    report({"check": "adverse_prior_and_history", "expected": expected_adverse,
            "observed": adverse, "pass": True})

    try:
        reference_support([], (1.0, 0.0), [])
    except ValueError as exc:
        report({"check": "empty_candidates", "expected": "reject; no uniform repair",
                "observed": str(exc), "pass": True})
    else:
        raise AssertionError("empty candidates were silently converted to support")
    return observed


def angular_residual(z: np.ndarray) -> float:
    approach = Rotation.from_rotvec(A * z[3:]).apply([1.0, 0.0, 0.0])
    target_axis = np.array([math.cos(A), math.sin(A), 0.0])
    return math.acos(float(np.clip(np.dot(approach, target_axis), -1.0, 1.0)))


def solve_case(
    name: str,
    current_xy: tuple[float, float],
    previous: Sequence[float],
    alpha: float,
    penalty: Penalty,
    expected: Sequence[float],
    residual: Callable[[np.ndarray], float] | None = None,
    expected_residual: float | None = None,
    residual_unit: str | None = None,
) -> None:
    current = np.asarray(current_xy, dtype=float)
    previous_array = np.asarray(previous, dtype=float)
    expected_array = np.asarray(expected, dtype=float)
    check(abs(float(np.linalg.norm(current)) - 1.0) <= WEIGHT_ATOL,
          "these analytic cases require unit input")

    def objective(z: np.ndarray) -> float:
        tracking = float(np.dot(z[:2] - current, z[:2] - current))
        smoothing = 0.1 * float(np.dot(z - previous_array, z - previous_array))
        return tracking + smoothing + alpha * penalty(z)

    def constraints(z: np.ndarray) -> np.ndarray:
        return np.array([
            1.0 - np.dot(z[:3], z[:3]),
            1.0 - np.dot(z[3:], z[3:]),
            np.dot(current, z[:2]),
        ], dtype=float)

    result = minimize(
        objective,
        np.zeros(6),
        method="SLSQP",
        bounds=[(-1.0, 1.0)] * 6,
        constraints=[{"type": "ineq", "fun": constraints}],
        options=OPTIONS,
    )
    observed = np.asarray(result.x, dtype=float)
    check(bool(result.success), f"{name}: solver failed: {result.message}")
    check(bool(np.all(np.isfinite(observed))) and math.isfinite(float(result.fun)),
          f"{name}: nonfinite solver result")
    margins = constraints(observed)
    check(bool(np.all(np.isfinite(margins))) and float(np.min(margins)) >= -FEASIBILITY_ATOL,
          f"{name}: norm or current-direction constraint violated: {margins}")
    check(float(np.max(np.abs(observed))) <= 1.0 + FEASIBILITY_ATOL,
          f"{name}: coordinate bound violated")
    position_error = float(np.linalg.norm(L * (observed[:3] - expected_array[:3])))
    rotation_error = float((
        Rotation.from_rotvec(A * observed[3:])
        * Rotation.from_rotvec(A * expected_array[3:]).inv()
    ).magnitude())
    check(position_error <= POSITION_ATOL_M,
          f"{name}: position oracle mismatch: {position_error} m")
    check(rotation_error <= ANGLE_ATOL_RAD,
          f"{name}: rotation oracle mismatch: {rotation_error} rad")
    output: dict[str, object] = {
        "check": name,
        "expected_normalized_increment": expected_array.tolist(),
        "observed_normalized_increment": observed.tolist(),
        "expected_delta_position_m": (L * expected_array[:3]).tolist(),
        "observed_delta_position_m": (L * observed[:3]).tolist(),
        "expected_rotation_vector_rad": (A * expected_array[3:]).tolist(),
        "observed_rotation_vector_rad": (A * observed[3:]).tolist(),
        "position_error_m": position_error,
        "rotation_error_rad": rotation_error,
        "constraint_margins": margins.tolist(),
        "solver_success": bool(result.success),
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "iterations": int(result.nit),
        "objective": float(result.fun),
        "pass": True,
    }
    if residual is not None:
        check(expected_residual is not None and residual_unit in {"m", "rad"},
              "residual oracle must include value and unit")
        observed_residual = float(residual(observed))
        tolerance = POSITION_ATOL_M if residual_unit == "m" else ANGLE_ATOL_RAD
        check(math.isfinite(observed_residual)
              and abs(observed_residual - expected_residual) <= tolerance,
              f"{name}: residual oracle mismatch: {observed_residual}")
        check(observed_residual > 0, f"{name}: expected a positive remaining soft residual")
        output.update({"expected_residual": expected_residual,
                       "observed_residual": observed_residual,
                       "residual_unit": residual_unit})
    report(output)


def main() -> None:
    report({
        "purpose": "standalone numerical design checks; no component, real-time or robot validation",
        "python": platform.python_version(), "platform": platform.platform(),
        "machine": platform.machine(), "numpy": np.__version__, "scipy": scipy.__version__,
        "solver": "SLSQP", "solver_options": OPTIONS,
        "L_m": L, "A_rad": A,
        "position_atol_m": POSITION_ATOL_M, "angle_atol_rad": ANGLE_ATOL_RAD,
        "weight_atol": WEIGHT_ATOL, "feasibility_atol_dimensionless": FEASIBILITY_ATOL,
    })
    weights = verify_support()
    zero = [0.0] * 6
    solve_case("joystick_only", (1.0, 0.0), zero, 0.0, lambda z: 0.0,
               [10 / 11, 0, 0, 0, 0, 0])

    def approach_residual(z: np.ndarray) -> float:
        return float(np.linalg.norm(L * z[:3] - [2 * L, 0.0, 0.0])) - L

    solve_case("positive_residual_approach", (1.0, 0.0), zero, 0.5,
               lambda z: max(0.0, approach_residual(z) / L) ** 2,
               [15 / 16, 0, 0, 0, 0, 0], approach_residual, L / 16, "m")

    def reversal_penalty(z: np.ndarray) -> float:
        position = L * z[:3]
        return (
            weights["left"] * max(0.0, float(np.linalg.norm(position - [-L, 0, 0])) / L) ** 2
            + weights["right"] * max(0.0, float(np.linalg.norm(position - [L, 0, 0])) / L) ** 2
        )

    solve_case("reversal_with_real_previous_increment", (-1.0, 0.0),
               [1, 0, 0, 0, 0, 0], 0.5, reversal_penalty,
               [-87 / 128, 0, 0, 0, 0, 0])
    solve_case("tcp_x_axis_alignment", (1.0, 0.0), zero, 0.5,
               lambda z: max(0.0, angular_residual(z) / A) ** 2,
               [10 / 11, 0, 0, 0, 0, 5 / 6], angular_residual, A / 6, "rad")
    report({"result": "PASS", "support_checks": 5, "six_dimensional_solver_cases": 4})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise
