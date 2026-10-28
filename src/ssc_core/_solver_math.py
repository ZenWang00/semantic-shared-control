"""Fixed candidate-point position objective, evaluated only as trusted numerics.

Three dimensionless translation variables are optimized. Orientation is copied
from the measured snapshot. No model-generated function or code is evaluated.
"""
import math
import time
import numpy as np
from scipy.optimize import minimize

SLSQP_OPTIONS = {'eps': 1e-6, 'ftol': 1e-9, 'maxiter': 100, 'disp': False}
BASIC_MARGIN_NAMES = ('translation_norm', 'input_half_space')
BOX_MARGIN_NAMES = tuple(f'translation_{axis}_{side}' for axis in 'xyz' for side in ('lower', 'upper'))

class SolveFailure(Exception):
    def __init__(self, status, code, diagnostic, *, evaluation_valid=True):
        super().__init__(diagnostic)
        self.status, self.code, self.diagnostic = status, code, diagnostic
        self.evaluation_valid = evaluation_valid

def candidate_pose(problem, x):
    return (np.asarray(problem['p_tcp'], dtype=np.float64) + problem['L_m'] * x,
            np.asarray(problem['q_tcp'], dtype=np.float64))

def basic_margins(problem, x):
    u = np.asarray(problem['u_xy'], dtype=np.float64)
    magnitude = math.hypot(*u)
    direction = u / magnitude if magnitude else np.zeros(2)
    values = [magnitude * magnitude - float(x @ x), float(direction @ x[:2])]
    for component in x:
        values.extend((1. + float(component), 1. - float(component)))
    return dict(zip(BASIC_MARGIN_NAMES + BOX_MARGIN_NAMES, values))

def emitted_increment(problem, position):
    return (np.asarray(position) - np.asarray(problem['p_tcp'])) / problem['L_m']

def candidate_diagnostic(problem, position):
    distance = math.dist(position, problem['candidate_position_xyz'])
    raw = distance - problem['distance_tolerance_m']
    penalty = (max(0., raw) / problem['distance_scale_m']) ** 2
    return {'candidate_id': problem['candidate_id'], 'distance_m': distance, 'raw_g': raw,
            'normalization_scale': problem['distance_scale_m'], 'penalty': penalty}

def objective_terms(problem, x, diagnostic):
    b = problem['xy_gain'] * np.asarray(problem['u_xy'], dtype=np.float64)
    tracking = float((x[:2] - b) @ (x[:2] - b))
    previous = np.asarray(problem['x_prev'], dtype=np.float64).copy()
    if problem['alpha'] == 0:
        previous[2] = 0.
    smoothing = problem['smoothing'] * float((x - previous) @ (x - previous))
    assistance = problem['alpha'] * diagnostic['penalty']
    return tracking, smoothing, assistance

def solve_problem(problem, host_margins, *, deadline_ns, clock_ns=time.monotonic_ns):
    counters = {'objective': 0, 'constraints': 0}
    tolerance, host_names = problem['hard_margin_tolerance'], []
    def check_deadline():
        if clock_ns() >= deadline_ns:
            raise SolveFailure('timeout', 'deadline', 'real-wall solve deadline expired', evaluation_valid=False)
    def objective(x):
        check_deadline()
        counters['objective'] += 1
        try:
            value = sum(objective_terms(problem, x, candidate_diagnostic(problem, candidate_pose(problem, x)[0])))
        except (OverflowError, ValueError):
            value = math.inf
        if not math.isfinite(value):
            raise SolveFailure('error', 'nonfinite_objective', 'objective is not finite', evaluation_valid=False)
        return value
    def margins_at(position, quaternion, x):
        basic = basic_margins(problem, x)
        names, values = host_margins(position.tolist(), quaternion.tolist())
        if (host_names and list(names) != host_names) or len(names) != len(values) or len(set(names)) != len(names):
            raise SolveFailure('error', 'host_margin_names', 'checker margin names changed or duplicated')
        host_names[:] = names
        return basic, dict(zip(names, values))
    def constraints(x):
        check_deadline()
        counters['constraints'] += 1
        basic, host = margins_at(*candidate_pose(problem, x), x)
        values = [basic[name] for name in BASIC_MARGIN_NAMES] + list(host.values())
        if not all(math.isfinite(value) for value in values):
            raise SolveFailure('error', 'nonfinite_margin', 'constraint margin is not finite')
        return np.asarray(values, dtype=np.float64)
    check_deadline()
    bounds = [(-1., 1.), (-1., 1.), (0., 0.) if problem['alpha'] == 0 else (-1., 1.)]
    try:
        solution = minimize(objective, np.zeros(3), method='SLSQP', jac=None, bounds=bounds,
                            constraints=[{'type': 'ineq', 'fun': constraints}], options=dict(SLSQP_OPTIONS))
    except SolveFailure:
        raise
    except Exception as exc:
        raise SolveFailure('error', 'solver_exception', type(exc).__name__) from None
    check_deadline()
    report = {'scipy_status': int(solution.status), 'scipy_message': str(solution.message)[:200],
              'nit': int(solution.nit), 'nfev': int(solution.nfev), 'njev': int(getattr(solution, 'njev', 0)),
              'objective_calls': counters['objective'], 'constraint_calls': counters['constraints']}
    x = np.asarray(solution.x, dtype=np.float64)
    if solution.status == 9:
        raise SolveFailure('timeout', 'iteration_limit', 'SLSQP iteration limit reached', evaluation_valid=False)
    if solution.status == 4:
        raise SolveFailure('infeasible', 'incompatible_constraints', report['scipy_message'])
    if not solution.success:
        if np.all(np.isfinite(x)):
            basic, host = margins_at(*candidate_pose(problem, x), x)
            violated = [name for name, value in list(basic.items()) + list(host.items()) if not value >= -tolerance]
            if violated:
                raise SolveFailure('infeasible', f'slsqp_status_{solution.status}_hard_infeasible', ', '.join(violated)[:200])
        raise SolveFailure('error', f'slsqp_status_{solution.status}', report['scipy_message'])
    if not (np.all(np.isfinite(x)) and math.isfinite(float(solution.fun))):
        raise SolveFailure('error', 'nonfinite_solution', 'solver returned a nonfinite point', evaluation_valid=False)
    position, quaternion = candidate_pose(problem, x)
    x = emitted_increment(problem, position)
    basic, host = margins_at(position, quaternion, x)
    diagnostic = candidate_diagnostic(problem, position)
    tracking, smoothing, assistance = objective_terms(problem, x, diagnostic)
    check_deadline()
    report.update({'x': x.tolist(), 'position_xyz': position.tolist(), 'quaternion_xyzw': quaternion.tolist(),
                   'objective': tracking + smoothing + assistance,
                   'terms': {'tracking': tracking, 'smoothing': smoothing, 'assistance': assistance},
                   'candidate_evaluation': diagnostic, 'basic_margins': sorted(basic.items()),
                   'host_margins': sorted(host.items())})
    violated = [name for name, value in list(basic.items()) + list(host.items()) if not value >= -tolerance]
    if violated:
        failure = SolveFailure('infeasible', 'hard_margin_violation', ', '.join(violated)[:200])
        failure.report = report
        raise failure
    return report
