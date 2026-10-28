"""Trusted numerical solver process; fixed objective and no generated code.

Launched in its own process group under a 1 GiB address-space limit. The host
owns deadlines, cancellation and retirement, including an unresponsive worker.
"""
import importlib
import resource
import sys
import time
from ._ipc import _decode, _encode, PAYLOAD_BYTES

ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
SOLVER_CONFIG_ID = 'solver-candidates-v2'
_RETAINED = {'ready', 'solved', 'infeasible'}

def _limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    actual = {'address_space': list(resource.getrlimit(resource.RLIMIT_AS)),
              'core': list(resource.getrlimit(resource.RLIMIT_CORE))}
    if actual != {'address_space': [ADDRESS_SPACE_BYTES] * 2, 'core': [0, 0]}:
        raise RuntimeError('resource limits not applied')
    return actual

def _solve(problem, host_margins, deadline_ns):
    from ._solver_math import solve_problem
    return solve_problem(problem, host_margins, deadline_ns=deadline_ns)

def _load_checker(spec):
    module_name, _, attribute = spec.partition(':')
    checker = getattr(importlib.import_module(module_name), attribute)()
    if not callable(checker):
        raise TypeError('checker factory did not return a callable')
    return checker

class _Worker:
    def __init__(self, limits):
        self.limits, self.generation, self.checker = limits, None, None
        self.candidates, self.environment = {}, {}
    def prepare(self, request):
        if self.generation is not None or request['solver_config_id'] != SOLVER_CONFIG_ID:
            raise ValueError('invalid lifecycle or unsupported solver configuration')
        import numpy
        import scipy
        importlib.import_module(f'{__package__}._solver_math')
        self.environment = {'numpy': numpy.__version__, 'scipy': scipy.__version__}
        self.checker = _load_checker(request['checker'])
        self.candidates = {c['candidate_id']: c for c in request['candidates']}
        if not 1 <= len(self.candidates) <= 16 or len(self.candidates) != len(request['candidates']):
            raise ValueError('invalid candidate set')
        self.generation = request['generation']
        return {'status': 'ready', 'code': 'ready', 'candidate_ids': sorted(self.candidates),
                'environment': self.environment}
    def solve(self, request):
        from ._solver_math import SolveFailure
        from .host_constraints import HostConstraintResult, MeasuredStateView
        from .policy import NUMERIC_CANDIDATES_V2
        from .records import Pose, SessionConfig
        from .validation import parse_record
        if self.generation != request['generation']:
            raise ValueError('invalid worker lifecycle')
        problem, deadline = request['problem'], request['deadline_ns']
        candidate = self.candidates.get(problem['candidate_id'])
        if candidate is None or candidate['position_xyz'] != problem['candidate_position_xyz']:
            raise ValueError('candidate differs from prepared context')
        state = problem['measured_state']
        view = MeasuredStateView(state['frame_id'], state['tcp_frame'],
            parse_record(Pose, state['tcp_pose'], policy=NUMERIC_CANDIDATES_V2),
            tuple(state['joint_names']), tuple(state['joint_positions_rad']))
        config = parse_record(SessionConfig, problem['configuration'], policy=NUMERIC_CANDIDATES_V2)
        def host_margins(position, quaternion):
            try:
                pose = parse_record(Pose, {'position_xyz': position, 'quaternion_xyzw': quaternion},
                                    policy=NUMERIC_CANDIDATES_V2)
                result = self.checker(candidate_pose=pose, measured_state=view,
                    u_xy=tuple(problem['u_xy']), dt_s=problem['dt_s'], configuration=config)
                if type(result) is not HostConstraintResult:
                    raise TypeError('checker must return HostConstraintResult')
            except Exception as exc:
                raise SolveFailure('error', 'host_checker', type(exc).__name__) from None
            return [m.name for m in result.margins], [float(m.value) for m in result.margins]
        try:
            return {'status': 'solved', 'code': 'solved', 'evaluation_valid': True,
                    'report': _solve(problem, host_margins, deadline)}
        except SolveFailure as failure:
            return {'status': failure.status, 'code': failure.code, 'diagnostic': failure.diagnostic,
                    'evaluation_valid': failure.evaluation_valid, 'report': getattr(failure, 'report', None)}

def main():
    limits = _limits()
    worker = _Worker(limits)
    while True:
        line = sys.stdin.buffer.readline(PAYLOAD_BYTES + 2)
        if not line or len(line) > PAYLOAD_BYTES + 1 or not line.endswith(b'\n'):
            return
        request = _decode(line)
        started = time.monotonic_ns()
        reply = {'request_id': request['request_id'], 'generation': request['generation'],
                 'operation': request['operation'], 'status': 'error', 'code': 'worker_exception',
                 'diagnostic': '', 'evaluation_valid': False, 'report': None, 'environment': None}
        try:
            if request['operation'] == 'prepare':
                reply.update(worker.prepare(request))
            elif request['operation'] == 'solve':
                reply.update(worker.solve(request))
            else:
                raise ValueError('unsupported operation')
        except MemoryError:
            reply.update(status='timeout', code='memory_limit', diagnostic='allocation refused under RLIMIT_AS')
        except Exception as exc:
            reply.update(status='error', code='worker_exception', diagnostic=type(exc).__name__)
        reply['worker_elapsed_s'] = (time.monotonic_ns() - started) / 1e9
        reply['limits'] = limits
        sys.stdout.buffer.write(_encode(reply))
        sys.stdout.buffer.flush()
        if reply['status'] not in _RETAINED:
            sys.stdin.buffer.read(1)
            return

if __name__ == '__main__':
    main()
