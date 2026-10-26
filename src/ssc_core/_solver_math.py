"""solver-slsqp-v1 numerics for offline-reference-v1 section 4 (NumPy/SciPy).

Imported only inside the supervised solver process, tests and evidence scripts;
``import ssc_core`` never loads it. ``solve_problem`` takes one validated plain
problem plus two injected callbacks: ``evaluate(term, p_tcp, R_control_tcp)``
returns one generated residual, and ``host_margins(pose, position, quaternion)``
returns the injected checker's named margins. It returns a plain report and
never builds a command or grants authority.
"""

import math
import time

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

from .records import Pose

SLSQP_OPTIONS = {"eps": 1e-6, "ftol": 1e-9, "maxiter": 100, "disp": False}
BASIC_MARGIN_NAMES = ("translation_norm", "rotation_norm", "input_half_space")
BOX_MARGIN_NAMES = tuple(f"{kind}_{axis}_{side}" for kind in ("translation", "rotation")
                         for axis in "xyz" for side in ("lower", "upper"))


class SolveFailure(Exception):
    """A classified failure; ``status`` is timeout, infeasible or error."""

    def __init__(self, status, code, diagnostic, *, evaluation_valid=True, evaluator=None):
        super().__init__(diagnostic)
        self.status, self.code, self.diagnostic = status, code, diagnostic
        self.evaluation_valid, self.evaluator = evaluation_valid, evaluator


def canonical_quaternion(rotation):
    """Normalized xyzw quaternion with a nonnegative scalar component."""
    quaternion = rotation.as_quat()
    quaternion = quaternion / np.linalg.norm(quaternion)
    return -quaternion if quaternion[3] < 0 else quaternion


def candidate_pose(problem, z):
    """p' = p + L x and R' = Exp([A r]x) R, left-multiplied in the control frame."""
    position = np.asarray(problem["p_tcp"], dtype=np.float64) + problem["L_m"] * z[:3]
    rotation = Rotation.from_rotvec(problem["A_rad"] * z[3:]) * Rotation.from_quat(problem["q_tcp"])
    return position, rotation


def basic_margins(problem, x, r):
    """Section 4 norm/half-space margins and the six componentwise bounds."""
    u = np.asarray(problem["u_xy"], dtype=np.float64)
    magnitude = math.hypot(*problem["u_xy"])
    direction = u / magnitude if magnitude else np.zeros(2)
    values = [magnitude * magnitude - float(x @ x), magnitude * magnitude - float(r @ r),
              float(direction @ x[:2])]
    for component in np.concatenate((x, r)):
        values.extend((1.0 + float(component), 1.0 - float(component)))
    return dict(zip(BASIC_MARGIN_NAMES + BOX_MARGIN_NAMES, values))


def emitted_increment(problem, position, quaternion):
    """Recover x, r from an emitted target exactly as the gate does."""
    x = (np.asarray(position) - np.asarray(problem["p_tcp"])) / problem["L_m"]
    relative = Rotation.from_quat(quaternion) * Rotation.from_quat(problem["q_tcp"]).inv()
    return x, relative.as_rotvec() / problem["A_rad"]


def objective_terms(problem, z, residuals):
    """Dimensionless tracking, smoothing and weighted squared-hinge terms."""
    gain = problem["xy_gain"]
    b = gain * np.asarray(problem["u_xy"], dtype=np.float64)
    tracking = float((z[:2] - b) @ (z[:2] - b))
    previous = np.asarray(problem["x_prev"] + problem["r_prev"], dtype=np.float64)
    smoothing = problem["smoothing"] * float((z - previous) @ (z - previous))
    assistance = 0.0
    for term, g in zip(problem["terms"], residuals):
        hinge = max(0.0, g) / term["scale"]
        assistance += term["object_weight"] * term["binding_weight"] * hinge * hinge
    assistance *= problem["alpha"]
    return tracking, smoothing, assistance


def solve_problem(problem, evaluate, host_margins, *, deadline_ns, clock_ns=time.monotonic_ns,
                  evaluate_batch=None):
    """Run one bounded SLSQP solve; return a plain report or raise SolveFailure.

    The evaluate callback must raise SolveFailure for every evaluator failure;
    it receives the absolute deadline through its caller. Iteration exhaustion
    is a budget timeout. A finished point is rechecked from the emitted pose.
    """
    counters = {"objective": 0, "constraints": 0, "evaluations": 0}
    tolerance = problem["hard_margin_tolerance"]
    host_names = []
    cache = {}

    def check_deadline():
        if clock_ns() >= deadline_ns:
            raise SolveFailure("timeout", "deadline", "real-wall solve deadline expired",
                               evaluation_valid=False)

    def residuals_at(position, rotation):
        matrix = rotation.as_matrix().tolist()
        if evaluate_batch is not None:
            check_deadline()
            counters["evaluations"] += len(problem["terms"])
            return evaluate_batch(problem["terms"], position.tolist(), matrix)
        values = []
        for term in problem["terms"]:
            check_deadline()
            counters["evaluations"] += 1
            values.append(evaluate(term, position.tolist(), matrix))
        return values

    def residuals(z):
        key = z.tobytes()
        if key not in cache:
            cache.clear()  # SLSQP revisits only the latest point; keep memory bounded.
            cache[key] = residuals_at(*candidate_pose(problem, z))
        return cache[key]

    def objective(z):
        check_deadline()
        counters["objective"] += 1
        value = sum(objective_terms(problem, z, residuals(z)))
        if not math.isfinite(value):
            raise SolveFailure("error", "nonfinite_objective", "objective is not finite",
                               evaluation_valid=False)
        return value

    def margins_at(position, rotation, x, r):
        basic = basic_margins(problem, x, r)
        names, values = host_margins(position.tolist(), canonical_quaternion(rotation).tolist())
        if host_names and list(names) != host_names:
            raise SolveFailure("error", "host_margin_names", "checker margin names changed")
        host_names[:] = names
        return basic, dict(zip(names, values))

    def constraints(z):
        check_deadline()
        counters["constraints"] += 1
        position, rotation = candidate_pose(problem, z)
        basic, host = margins_at(position, rotation, z[:3], z[3:])
        values = [basic[name] for name in BASIC_MARGIN_NAMES] + list(host.values())
        if not all(math.isfinite(value) for value in values):
            raise SolveFailure("error", "nonfinite_margin", "constraint margin is not finite")
        return np.asarray(values, dtype=np.float64)

    check_deadline()
    try:
        # Zero initialization for every snapshot: no hidden warm-start state.
        solution = minimize(objective, np.zeros(6), method="SLSQP", jac=None,
                            bounds=[(-1.0, 1.0)] * 6,
                            constraints=[{"type": "ineq", "fun": constraints}],
                            options=dict(SLSQP_OPTIONS))
    except SolveFailure:
        raise
    except Exception as exc:  # A SciPy/numeric exception is never a partial solve.
        raise SolveFailure("error", "solver_exception", type(exc).__name__) from None
    check_deadline()
    report = {"scipy_status": int(solution.status), "scipy_message": str(solution.message)[:200],
              "nit": int(solution.nit), "nfev": int(solution.nfev), "njev": int(getattr(solution, "njev", 0)),
              "objective_calls": counters["objective"], "constraint_calls": counters["constraints"]}
    z = np.asarray(solution.x, dtype=np.float64)
    if solution.status == 9:
        raise SolveFailure("timeout", "iteration_limit", "SLSQP iteration limit reached", evaluation_valid=False)
    if solution.status == 4:
        raise SolveFailure("infeasible", "incompatible_constraints", report["scipy_message"])
    if not solution.success:
        # A returned hard-infeasible point is infeasible; any other failure is an error.
        if np.all(np.isfinite(z)):
            position, rotation = candidate_pose(problem, z)
            basic, host = margins_at(position, rotation, z[:3], z[3:])
            violated = [name for name, value in list(basic.items()) + list(host.items())
                        if not value >= -tolerance]
            if violated:
                raise SolveFailure("infeasible", f"slsqp_status_{solution.status}_hard_infeasible",
                                   ", ".join(violated)[:200])
        raise SolveFailure("error", f"slsqp_status_{solution.status}", report["scipy_message"])
    if not (np.all(np.isfinite(z)) and math.isfinite(float(solution.fun))):
        raise SolveFailure("error", "nonfinite_solution", "solver returned a nonfinite point",
                           evaluation_valid=False)

    # Independent final recomputation from the emitted, normalized target.
    position, rotation = candidate_pose(problem, z)
    quaternion = canonical_quaternion(rotation)
    position = np.asarray(position, dtype=np.float64)
    if not (np.all(np.isfinite(position)) and np.all(np.isfinite(quaternion))):
        raise SolveFailure("error", "nonfinite_pose", "candidate pose is not finite", evaluation_valid=False)
    emitted = Rotation.from_quat(quaternion)
    x, r = emitted_increment(problem, position, quaternion)
    basic, host = margins_at(position, emitted, x, r)
    final_residuals = residuals_at(position, emitted)
    tracking, smoothing, assistance = objective_terms(problem, np.concatenate((x, r)), final_residuals)
    check_deadline()
    report.update({
        "x": x.tolist(), "r": r.tolist(), "position_xyz": position.tolist(),
        "quaternion_xyzw": quaternion.tolist(),
        "objective": tracking + smoothing + assistance,
        "terms": {"tracking": tracking, "smoothing": smoothing, "assistance": assistance},
        "evaluations": [{"program_id": term["program_id"], "raw_g": g}
                        for term, g in zip(problem["terms"], final_residuals)],
        "basic_margins": sorted(basic.items()), "host_margins": sorted(host.items()),
        "evaluator_calls": counters["evaluations"],
    })
    violated = [name for name, value in list(basic.items()) + list(host.items()) if not value >= -tolerance]
    if violated:
        failure = SolveFailure("infeasible", "hard_margin_violation", ", ".join(violated)[:200])
        failure.report = report
        raise failure
    return report


def pose_record(position, quaternion):
    """A plain Pose value for callbacks; callers parse it at their boundary."""
    return Pose(tuple(float(v) for v in position), tuple(float(v) for v in quaternion))
