"""Contract validation and real Linux solver isolation/lifecycle regression."""
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import pytest
from ssc_core import (AppliedCommandReference, HostCheckerSpec, OptimizationInputError,
    SupervisedOptimizer, build_request, get_intent_config, get_solver_config, to_dict)

F=runpy.run_path(str(Path(__file__).resolve().parents[2]/'docs/reference/optimizer_fixtures.py'))
scenario, point = F['scenario'], F['point']
DOUBLE=HostCheckerSpec('ssc_core._test_doubles:WorkspaceSphereDouble')
linux=pytest.mark.skipif(sys.platform!='linux',reason='solver isolation acceptance is Linux-only')

def request(case,**kwargs):
    return build_request(case['resolved'],case['selection'],intent_config=case['intent_config'],
        command_seq=kwargs.pop('command_seq',5),previous_applied=case['previous'],**kwargs)
def code_of(case,**kwargs):
    with pytest.raises(OptimizationInputError) as error: request(case,**kwargs)
    return error.value.code

def test_request_is_canonical_and_has_only_selected_target():
    case=scenario(); first=request(case,result_id='r1'); second=request(case,result_id='r1')
    assert first==second
    assert first.selection_id=='selection-optimizer-fixture'
    problem=first.problem()
    assert problem['L_m']==pytest.approx(.01)
    assert problem['candidate_id']=='candidate-bottle-v2'
    assert problem['candidate_position_xyz']==[.41,0,.3]
    assert problem['alpha']==.5 and problem['smoothing']==.1
    assert 'terms' not in problem and 'A_rad' not in problem and 'r_prev' not in problem
    assert problem['distance_scale_m']==.01 and problem['distance_tolerance_m']==0

@pytest.mark.parametrize('kwargs',[{'current':(0,0)},{'enabled':False}])
def test_neutral_or_disabled_never_reaches_solver(kwargs):
    assert code_of(scenario(previous={'kind':'move'},**kwargs))=='input_not_active'

def test_selection_must_match_recomputed_snapshot():
    case=scenario(); selection=case['selection']
    for changed in (replace(selection,assistance_strength=.25),
                    replace(selection,selected_candidate_id='candidate-cup-v2'),
                    replace(selection,snapshot_id='other')):
        assert code_of(dict(case,selection=changed))=='selection_mismatch'
    assert code_of(dict(case,intent_config=replace(case['intent_config'],alpha_active=.7)))=='invalid_config'

def test_previous_reference_uses_translation_and_ignores_angular_history():
    case=scenario(previous={'velocity':(.01,.02,.03),'angular':(.1,.2,.3)})
    problem=request(case).problem()
    assert problem['x_prev']==pytest.approx([.2,.4,.6])
    assert 'r_prev' not in problem
    disabled=request(scenario(alpha_zero=True,previous={'velocity':(0,0,.03)})).problem()
    assert disabled['x_prev'][2]==0
    assert request(scenario(previous={'kind':'hold'})).previous_reference=='hold'
    assert request(scenario(previous={'epoch':0},control_epoch=1)).previous_reference=='old_epoch'
    for previous in ({'accepted':False},{'session':'wrong'},{'epoch':2}):
        assert code_of(scenario(previous=previous))=='previous_reference'
    missing=scenario(previous={'kind':'move'})
    assert code_of(dict(missing,previous=None))=='previous_reference'
    assert code_of(dict(missing,previous=AppliedCommandReference(missing['previous'].result,
        missing['previous'].acceptance,None)))=='previous_reference'

def test_prepared_candidate_context_is_exact():
    case=scenario(); candidates={p.candidate_id:to_dict(p) for p in case['resolved'].bundle.candidates}
    assert request(case,prepared_candidates=candidates)
    assert code_of(case,prepared_candidates={})=='context_mismatch'
    changed={k:dict(v) for k,v in candidates.items()}; changed['candidate-bottle-v2']['position_xyz']=[.42,0,.3]
    assert code_of(case,prepared_candidates=changed)=='context_mismatch'
    assert code_of(case,solver_config=replace(get_solver_config(),maxiter=50))=='invalid_config'
    for old in ('solver-slsqp-v1','solver-slsqp-batch-v2'):
        with pytest.raises(ValueError): get_solver_config(old)

@pytest.mark.parametrize('count',[1,2,16])
def test_candidate_count_is_not_generated_program_count(count):
    points=[point((.41,0,.3+i*.0001),f'candidate-{i}') for i in range(count)]
    case=scenario(candidate_points=points)
    assert request(case).problem()['candidate_id']=='candidate-0'

def test_invalid_input_returns_reasoned_not_run_hold():
    solver=SupervisedOptimizer(DOUBLE); case=scenario()
    changed=replace(case['selection'],assistance_strength=0)
    result=solver.solve(case['resolved'],changed,intent_config=case['intent_config'],command_seq=2).result(1)
    assert result.code=='selection_mismatch' and result.result.status=='hold'
    assert result.result.solve_time_s==0 and result.result.next_tcp_pose is None
    assert solver.close().result(2).reaped

def prepared(case,spec=DOUBLE):
    solver=SupervisedOptimizer(spec)
    outcome=solver.prepare(case['resolved'].bundle,motion_disabled=True).result(10)
    assert outcome.ok,outcome
    return solver

def run(solver,case,**kwargs):
    return solver.solve(case['resolved'],case['selection'],intent_config=case['intent_config'],
        command_seq=kwargs.pop('command_seq',9),previous_applied=case['previous'],**kwargs).result(5)

@linux
def test_preparation_limits_no_evaluator_children_and_lifecycle():
    case=scenario(); solver=prepared(case)
    try:
        pid=solver._process.pid
        assert pid!=os.getpid() and os.getpgid(pid)==pid
        assert '1073741824' in Path(f'/proc/{pid}/limits').read_text()
        assert b'OPENBLAS_NUM_THREADS=1\0' in Path(f'/proc/{pid}/environ').read_bytes()
        assert live_members(pid)==[pid]
        fresh=SupervisedOptimizer(DOUBLE)
        assert run(fresh,case).code=='not_prepared'
        assert fresh.prepare(case['resolved'].bundle,motion_disabled=False).result(1).code=='motion_not_disabled'
        assert fresh.close().result(2).reaped
    finally: solver.close().result(3)

@linux
@pytest.mark.parametrize('height,sign',[(.31,1),(.3,0),(.29,-1)])
def test_real_solver_height_assistance_and_orientation(height,sign):
    q=(.6,0,0,.8)
    case=scenario(candidate_points=[point((.41,0,height))],quaternion=q)
    solver=prepared(case)
    try:
        outcome=run(solver,case); result=outcome.result
        assert result.status=='move',outcome
        assert result.next_tcp_pose.quaternion_xyzw==q
        dz=result.next_tcp_pose.position_xyz[2]-.3
        assert abs(dz)<1e-7 if sign==0 else sign*dz>1e-3
        assert result.candidate_evaluation.candidate_id=='candidate-bottle-v2'
        assert 0<result.solve_time_s<.15 and solver.ready
    finally: solver.close().result(3)

@linux
def test_alpha0_drops_previous_z_and_rotational_motion():
    case=scenario(alpha_zero=True,previous={'velocity':(0,0,.04),'angular':(0,0,.3)})
    solver=prepared(case)
    try:
        outcome=run(solver,case); result=outcome.result
        assert result.status=='move',outcome
        assert result.next_tcp_pose.position_xyz[2]==.3
        assert result.next_tcp_pose.quaternion_xyzw==(0,0,0,1)
        assert json.loads(outcome.report_json)['terms']['assistance']==0
    finally: solver.close().result(3)

@linux
def test_reversal_and_repeat_are_deterministic():
    case=scenario(current=(-1,0),previous={'kind':'move'})
    solver=prepared(case)
    try:
        first,second=run(solver,case),run(solver,case)
        assert first.result.status=='move',first
        assert first.result.next_tcp_pose==second.result.next_tcp_pose
        assert first.result.next_tcp_pose.position_xyz[0]-.4==pytest.approx(-.00875,abs=1e-6)
    finally: solver.close().result(3)

@linux
def test_workspace_constraint_and_infeasible_start():
    case=scenario(tcp=(.495,0,.3),candidate_points=[point((.53,0,.3))])
    solver=prepared(case)
    try:
        outcome=run(solver,case)
        assert outcome.result.status=='move',outcome
        assert outcome.result.next_tcp_pose.position_xyz[0]<=.5+2e-9
    finally: solver.close().result(3)
    case=scenario(**F['infeasible_start']()); solver=prepared(case)
    try:
        result=run(solver,case).result
        assert result.status=='hold' and result.solver_status in ('infeasible','timeout')
        assert result.next_tcp_pose is None
    finally: solver.close().result(3)

@linux
def test_caller_deadline_only_shortens_budget():
    case=scenario(); solver=prepared(case)
    try:
        outcome=run(solver,case,deadline_ns=time.monotonic_ns()+10**12)
        assert outcome.result.status=='move' and outcome.deadline_ns-outcome.started_ns==150_000_000
        outcome=run(solver,case,deadline_ns=time.monotonic_ns()-1)
        assert outcome.result.status=='hold' and outcome.result.solver_status=='timeout'
        assert outcome.reaped and not solver.ready
    finally: solver.close().result(3)

@linux
@pytest.mark.parametrize('spec',['ssc_core._test_doubles:RaisingDouble','ssc_core._test_doubles:NotACheckerResult'])
def test_checker_failure_rejects(spec):
    case=scenario(); solver=prepared(case,HostCheckerSpec(spec))
    try:
        outcome=run(solver,case)
        assert outcome.result.status=='hold' and outcome.code=='host_checker'
    finally: solver.close().result(3)

inject = F["inject"]


def live_members(pgid):
    """Running (non-zombie) processes in a group; orphaned zombies await PID 1."""
    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except (OSError, IndexError):
                continue
            if int(fields[2]) == pgid and fields[0] != "Z":
                members.append(int(entry.name))
    return members


def group_gone(pgid, wait_s=1.):
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if not live_members(pgid):
            return True
        time.sleep(.01)
    return False


@linux
@pytest.mark.parametrize("invalid_result", [False, True])
def test_host_only_checker_failure_retires_real_process_group(invalid_result):
    case = scenario()
    solver = prepared(case)
    pgid = solver._process.pid
    def fail(**kwargs):
        if invalid_result:
            return None
        raise RuntimeError("host-only final recheck failure")
    solver._host_checker = fail  # worker still has its valid, independent checker
    try:
        outcome = run(solver, case)
        assert (outcome.result.status, outcome.result.solver_status, outcome.code) == ("hold", "error", "host_checker")
        assert outcome.result.next_tcp_pose is None and outcome.result.horizon_s is None
        assert outcome.reaped is True and not solver.ready and group_gone(pgid)
        assert run(solver, case).code == "not_prepared"
        assert solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10).ok
        assert run(solver, case).result.status == "move"
    finally:
        solver.close().result(3)


@linux
@pytest.mark.parametrize("mode,status,code", [
    ("stall", "timeout", "timeout"), ("late", "timeout", "timeout"), ("crash", "error", "worker_crash"),
    ("wrong_id", "error", "malformed_response"), ("bad_limits", "error", "malformed_response"),
    ("bad_quaternion", "error", "malformed_response"), ("drop_evaluation", "error", "malformed_response"),
    ("lie_pose", "infeasible", "host_recheck"),
    ("rotate", "infeasible", "orientation_changed"),
])
def test_faults_become_reasoned_holds(tmp_path, monkeypatch, mode, status, code):
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    inject(solver, tmp_path, mode, monkeypatch)
    preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
    assert preparation.ok, preparation
    pgid = preparation.worker_pid
    heartbeat, stop = [], threading.Event()
    def beat():
        while not stop.is_set():
            heartbeat.append(time.monotonic())
            time.sleep(.001)
    thread = threading.Thread(target=beat)
    thread.start()
    submitted = time.monotonic()
    future = solver.solve(case["resolved"], case["selection"], intent_config=case["intent_config"], command_seq=3)
    returned = time.monotonic()
    outcome = future.result(5)
    stop.set()
    thread.join()
    result = outcome.result
    assert (result.status, result.solver_status) == ("hold", status) and outcome.code == code, outcome
    assert result.next_tcp_pose is None and result.horizon_s is None and result.constraints_satisfied is None
    assert returned - submitted < .05  # validation only; no wait for computation in the caller
    assert outcome.finished_ns - outcome.started_ns < 150_000_000 + 400_000_000  # budget + bounded teardown
    gaps = [b - a for a, b in zip(heartbeat, heartbeat[1:])]
    assert max(gaps) < .05  # host thread stayed responsive during the stall
    if status in ("timeout", "error"):
        assert not solver.ready and outcome.reaped is True and group_gone(pgid)
    else:
        assert solver.ready
    assert solver.close().result(3).reaped
    assert group_gone(pgid)


@linux
def test_close_withdraws_pending_solve(tmp_path, monkeypatch):
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    inject(solver, tmp_path, "stall", monkeypatch)
    pgid = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10).worker_pid
    future = solver.solve(case["resolved"], case["selection"], intent_config=case["intent_config"], command_seq=4)
    busy = solver.solve(case["resolved"], case["selection"], intent_config=case["intent_config"], command_seq=5)
    assert busy.result(1).code == "busy" and busy.result().result.solver_status == "not_run"
    solver.close()
    outcome = future.result(3)
    assert outcome.result.status == "hold" and outcome.code in ("cancelled", "timeout")
    assert group_gone(pgid)


@linux
def test_repeated_failures_do_not_leak_process_groups(tmp_path, monkeypatch):
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    inject(solver, tmp_path, "crash", monkeypatch)
    groups = []
    for _ in range(5):
        preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
        assert preparation.ok
        groups.append(preparation.worker_pid)
        outcome = solver.solve(case["resolved"], case["selection"], intent_config=case["intent_config"],
                               command_seq=1).result(5)
        assert outcome.code == "worker_crash"
    assert all(group_gone(pgid) for pgid in groups)
    assert solver.close().result(2).reaped
