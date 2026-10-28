from dataclasses import replace
from math import sqrt

import pytest

from ssc_core import (ControlResult, ControlSnapshot, NUMERIC_CANDIDATES_V2 as POLICY,
    RobotState, ValidationError, parse_record, to_dict)


def test_measured_pose_and_optional_velocities_round_trip(resolved):
    state=resolved.robot_state.payload
    assert parse_record(RobotState,to_dict(state),policy=POLICY)==state
    data=to_dict(state)
    assert 'joint_velocities_radps' not in data
    data['joint_velocities_radps']=[0]
    assert parse_record(RobotState,data,policy=POLICY).joint_velocities_radps==(0,)
    data['joint_velocities_radps']=[]
    with pytest.raises(ValidationError,match='dimension'):parse_record(RobotState,data,policy=POLICY)


def test_nonidentity_unit_quaternion_is_preserved(make_resolved):
    quaternion=(0.,0.,sqrt(.5),sqrt(.5))
    state=make_resolved(quaternion=quaternion).robot_state.payload
    assert parse_record(RobotState,to_dict(state),policy=POLICY).tcp_pose.quaternion_xyzw==quaternion


def test_snapshot_pins_nullable_previous_selection_and_rejects_current_in_history(resolved):
    data=to_dict(resolved.snapshot)
    assert data['previous_selection_id'] is None
    assert parse_record(ControlSnapshot,data,policy=POLICY)==resolved.snapshot
    data['joystick_history_refs']=[data['joystick_ref']]
    with pytest.raises(ValidationError):parse_record(ControlSnapshot,data,policy=POLICY)


def test_result_move_and_hold_have_distinct_contract(resolved):
    hold=ControlResult('result',None,None,1,1,None,None,'hold',False,'not_run',None,0.,None,reason='released')
    assert parse_record(ControlResult,to_dict(hold),policy=POLICY)==hold
    data=to_dict(hold);data['solve_time_s']=.1
    with pytest.raises(ValidationError):parse_record(ControlResult,data,policy=POLICY)
    move=replace(hold,snapshot_id='snapshot',selection_id='selected',source_joystick_seq=1,source_robot_state_seq=1,
                 status='move',evaluation_valid=True,solver_status='solved',constraints_satisfied=True,
                 next_tcp_pose=resolved.robot_state.payload.tcp_pose,horizon_s=.2,reason=None)
    assert parse_record(ControlResult,to_dict(move),policy=POLICY)==move
    data=to_dict(move);data['source_robot_state_seq']=None
    with pytest.raises(ValidationError):parse_record(ControlResult,data,policy=POLICY)
