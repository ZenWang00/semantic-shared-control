"""Synthetic candidate-point inputs and trusted solver fault injection.

No real VLM or robot measurements; generated-code fixtures are historical only.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

from ssc_core import (NUMERIC_CANDIDATES_V2, AppliedCommandReference, SessionRegistry,
                      estimate_intent, get_intent_config, parse_message, to_dict)
from ssc_core.records import CommandAcceptanceRecord, TimePoint

ROOT = Path(__file__).resolve().parents[2]
BOTTLE, CUP = 'object-bottle-offline-v1', 'object-cup-offline-v1'
ORIGIN = (.4, 0., .3)
L, A = .01, .1
CLASSIFICATION_BUDGET_S = 1.

def _stamp(ns):
    sec, nano = divmod(ns, 1_000_000_000)
    return {'clock_id': 'synthetic-clock-offline-v1', 'sec': sec, 'nanosec': nano}

def scenario(*, current=(1., 0.), enabled=True, history=(), relevance=(.5, .5), alpha_zero=False,
             tcp=ORIGIN, quaternion=(0., 0., 0., 1.), candidate_points=None,
             previous=None, control_epoch=0):
    catalog = json.loads((ROOT / 'tests/fixtures/shared_control/bottle_cup_candidates_v2.json').read_text())
    messages = {item['message_type']: item for item in catalog['messages']}
    config = messages['SessionConfig']['payload']
    if alpha_zero:
        config['config_id'] = 'config-candidates-alpha0-v2'
    semantic = messages['SemanticContext']['payload']
    candidate_set = messages['CandidateSet']['payload']
    if candidate_points is not None:
        candidate_set['candidates'] = [dict(point) for point in candidate_points]
    objects = {point['object_id'] for point in candidate_set['candidates']}
    semantic['object_relevance'] = [{'object_id': obj, 'task_relevance': score}
                                  for obj, score in zip((BOTTLE, CUP), relevance) if obj in objects]
    session_id = messages['SessionConfig']['meta']['session_id']
    registry = SessionRegistry(session_id, policy=NUMERIC_CANDIDATES_V2)
    for item in messages.values():
        registry.register_message(item)
    def message(kind, payload, identifier, seq, ns=10_000_000_000):
        return registry.register_message({'message_type': kind, 'payload': payload,
            'meta': {'schema_version': '2.0', 'session_id': session_id, 'message_id': identifier,
                     'source_id': 'synthetic-optimizer-input', 'sequence': seq, 'produced_at': _stamp(ns)}})
    def state(identifier, seq, position, quat, ns):
        return message('RobotState', {'sampled_at': _stamp(ns), 'frame_id': config['control_frame'],
            'tcp_frame': config['tcp_frame'], 'tcp_pose': {'position_xyz': list(position), 'quaternion_xyzw': list(quat)},
            'joint_names': catalog['initial_joint_names'], 'joint_positions_rad': catalog['initial_joint_positions_rad'],
            'state_source': 'fk_from_measured_joints'}, identifier, seq, ns)
    task, geometry = messages['TaskContext']['payload'], messages['SceneGeometry']['payload']
    base = {'config_id': config['config_id'], 'task_ref': {'task_id': task['task_id'], 'revision': task['revision']},
            'dt_s': .2, 'geometry_ref': {'geometry_id': geometry['geometry_id'], 'revision': geometry['revision']},
            'semantic_id': semantic['semantic_id'], 'candidate_set_id': candidate_set['candidate_set_id'],
            'previous_selection_id': None}
    applied_id, reference = None, None
    intent = get_intent_config('intent-candidates-alpha0-v2' if alpha_zero else 'intent-candidates-v2')
    if previous is not None:
        message('JoystickSample', {'sampled_at': _stamp(9_800_000_000), 'mapping_id': 'synthetic-intent-mapping',
                'u_xy': [1, 0], 'enabled': True}, 'previous-input', 1, 9_800_000_000)
        state('previous-state', 1, tcp, quaternion, 9_800_000_000)
        epoch = previous.get('epoch', control_epoch)
        registry.register_record('ControlSnapshot', dict(base, snapshot_id='snapshot-previous', snapshot_seq=1,
            control_epoch=epoch, joystick_ref='previous-input', robot_state_ref='previous-state',
            joystick_history_refs=[], previous_applied_command_id=None), session_id=session_id)
        source = registry.resolve_snapshot('snapshot-previous')
        velocity, angular = previous.get('velocity', (.05, 0, 0)), previous.get('angular', (0, 0, 0))
        from scipy.spatial.transform import Rotation
        target_q = (Rotation.from_rotvec([w * .2 for w in angular]) * Rotation.from_quat(quaternion)).as_quat().tolist()
        payload = {'result_id': 'result-previous', 'snapshot_id': 'snapshot-previous', 'selection_id': 'selection-previous',
                   'control_epoch': epoch, 'command_seq': 1, 'source_joystick_seq': 1, 'source_robot_state_seq': 1,
                   'evaluation_valid': True, 'solver_status': 'solved', 'constraints_satisfied': False,
                   'solve_time_s': .01, 'candidate_evaluation': None}
        if previous.get('kind', 'move') == 'move':
            payload.update(status='move', next_tcp_pose={'position_xyz': [float(p + v * .2) for p, v in zip(tcp, velocity)],
                                                        'quaternion_xyzw': target_q}, horizon_s=.2)
        else:
            payload.update(status='hold', evaluation_valid=False, solver_status='not_run', solve_time_s=0,
                           constraints_satisfied=None, reason='local hold')
        result_message = parse_message({'message_type': 'ControlResult', 'payload': payload,
            'meta': {'schema_version': '2.0', 'session_id': previous.get('session', session_id),
                     'message_id': 'previous-result', 'source_id': 'synthetic-optimizer', 'sequence': 1,
                     'produced_at': _stamp(9_900_000_000)}}, policy=NUMERIC_CANDIDATES_V2)
        applied_id = 'applied-previous'
        acceptance = CommandAcceptanceRecord('result-previous', previous.get('accepted', True), 'accepted',
            TimePoint(**_stamp(9_900_000_000)), applied_id if previous.get('accepted', True) else None)
        reference = AppliedCommandReference(result_message, acceptance, source)
    for index, (vector, on) in enumerate(history):
        ns = 10_000_000_000 - (len(history) - index) * 20_000_000
        message('JoystickSample', {'sampled_at': _stamp(ns), 'mapping_id': 'synthetic-intent-mapping',
                'u_xy': list(vector), 'enabled': on}, f'history-{index}', 10 + index, ns)
    message('JoystickSample', {'sampled_at': _stamp(10_000_000_000), 'mapping_id': 'synthetic-intent-mapping',
            'u_xy': list(current), 'enabled': enabled}, 'current-input', 100)
    state('measured-state', 100, tcp, quaternion, 10_000_000_000)
    registry.register_record('ControlSnapshot', dict(base, snapshot_id='snapshot-optimizer-fixture', snapshot_seq=2,
        control_epoch=control_epoch, joystick_ref='current-input', robot_state_ref='measured-state',
        joystick_history_refs=[f'history-{index}' for index in range(len(history))],
        previous_applied_command_id=applied_id), session_id=session_id)
    resolved = registry.resolve_snapshot('snapshot-optimizer-fixture')
    outcome = estimate_intent(resolved, intent, selection_id='selection-optimizer-fixture', policy=NUMERIC_CANDIDATES_V2)
    return {'resolved': resolved, 'selection': outcome.selection, 'disposition': outcome.disposition,
            'intent_config': intent, 'previous': reference, 'registry': registry, 'session_id': session_id}

def point(position, candidate_id='candidate-bottle-v2', object_id=BOTTLE):
    return {'candidate_id': candidate_id, 'object_id': object_id, 'position_xyz': list(position)}

def infeasible_start():
    return {'tcp': (.505, 0., .3), 'candidate_points': [point((.51, 0., .3))]}

@contextmanager
def relaxed_solve_budget(seconds=CLASSIFICATION_BUDGET_S):
    import ssc_core.optimization as optimization
    original = optimization.get_solver_config
    profile = original()
    relaxed = replace(profile, solve_budget_s=seconds)
    optimization.get_solver_config = lambda solver_config_id=profile.solver_config_id: relaxed if solver_config_id == profile.solver_config_id else original(solver_config_id)
    try:
        yield relaxed
    finally:
        optimization.get_solver_config = original

SOLVER_FAULT = '''
import os, signal, sys, time
from ssc_core import _solver_worker
mode = sys.argv[1]
original_solve, encode = _solver_worker._solve, _solver_worker._encode
def solve(problem, host_margins, deadline_ns):
    if mode == "stall": time.sleep(60)
    if mode == "late": time.sleep(max(0, (deadline_ns - time.monotonic_ns()) / 1e9) + .02)
    if mode == "crash": os._exit(17)
    report = original_solve(problem, host_margins, deadline_ns)
    if mode == "lie_pose": report["position_xyz"][0] = .6
    if mode == "bad_quaternion": report["quaternion_xyzw"] = [0, 0, 0, 2]
    if mode == "rotate": report["quaternion_xyzw"] = [0, 0, .01, (1-.01**2)**.5]
    if mode == "drop_evaluation": report["candidate_evaluation"] = None
    return report
def reply(data):
    if data.get("operation") == "solve":
        if mode == "wrong_id": data["request_id"] = "old"
        if mode == "bad_limits": data["limits"] = {"address_space": [0,0], "core": [0,0]}
    return encode(data)
if mode in ("stall", "late"): signal.signal(signal.SIGTERM, signal.SIG_IGN)
_solver_worker._solve, _solver_worker._encode = solve, reply
_solver_worker.main()
'''

def inject(solver, directory, mode, monkeypatch=None):
    path = Path(directory) / 'solver_fault.py'
    path.write_text(SOLVER_FAULT)
    original_popen = subprocess.Popen
    def spawn():
        environment = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
        solver._process = original_popen([sys.executable, '-I', str(path), mode], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, env=environment, start_new_session=True)
        os.set_blocking(solver._process.stdin.fileno(), False)
        os.set_blocking(solver._process.stdout.fileno(), False)
    if monkeypatch is not None:
        monkeypatch.setattr(solver, '_spawn', spawn)
    else:
        solver._spawn = spawn
