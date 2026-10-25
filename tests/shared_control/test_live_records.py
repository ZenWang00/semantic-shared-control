"""Live/control structural evidence; no resolver, controller or gate is run."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import json
import math

import pytest

import ssc_core
from ssc_core import (
    NUMERIC_OFFLINE_V1,
    ValidationError,
    ValidationPolicy,
    loads_message,
    parse_message,
    parse_record,
    to_dict,
)


LIVE_MESSAGES = (
    'JoystickSample', 'RobotState', 'ControlSnapshot', 'ObjectWeights',
    'ControlResult',
)
RECORD_CASES = tuple((name, name) for name in LIVE_MESSAGES) + (
    ('ControlResult', 'hold'),
    ('TaskRef', 'TaskRef'),
    ('ObjectWeight', 'ObjectWeight'),
    ('ConstraintEvaluation', 'ConstraintEvaluation'),
    ('CommandAcceptanceRecord', 'CommandAcceptanceRecord'),
)
MAX_COUNTER = 2**53 - 1


@pytest.fixture
def live_values(illustrative_catalog):
    """Select both result branches without collapsing repeated message types."""
    result = {}
    for message in illustrative_catalog['messages']:
        name = message['message_type']
        if name not in LIVE_MESSAGES:
            continue
        payload = deepcopy(message['payload'])
        if name == 'ControlResult' and payload['status'] == 'hold':
            result['hold'] = payload
        else:
            result.setdefault(name, payload)
    result['TaskRef'] = deepcopy(result['ControlSnapshot']['task_ref'])
    result['ObjectWeight'] = deepcopy(result['ObjectWeights']['weights'][0])
    result['ConstraintEvaluation'] = deepcopy(result['ControlResult']['constraint_evaluations'][0])
    result['CommandAcceptanceRecord'] = deepcopy(illustrative_catalog['command_acceptance_records'][0])
    return result


def parse(name, value, policy=NUMERIC_OFFLINE_V1):
    return parse_record(name, value, policy=policy)


def reject(name, value, path=None, policy=NUMERIC_OFFLINE_V1):
    with pytest.raises(ValidationError) as caught:
        parse(name, value, policy)
    error = caught.value
    assert error.path.startswith('$')
    assert error.code and error.message
    if path is not None:
        assert error.path == path
    return error


def change(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def assert_immutable(value):
    if is_dataclass(value):
        first = fields(value)[0]
        with pytest.raises(FrozenInstanceError):
            setattr(value, first.name, None)
        for field in fields(value):
            assert_immutable(getattr(value, field.name))
    elif type(value) is tuple:
        if value:
            with pytest.raises(TypeError):
                value[0] = None
        for item in value:
            assert_immutable(item)
    else:
        assert value is None or type(value) in (str, int, float, bool)


@pytest.mark.parametrize('name,key', RECORD_CASES)
def test_live_records_use_public_frozen_types_and_round_trip(live_values, name, key):
    source = live_values[key]
    original = deepcopy(source)
    parsed = parse(name, source)
    assert type(parsed) is getattr(ssc_core, name)
    assert name in ssc_core.__all__
    assert_immutable(parsed)
    assert to_dict(parsed) == original
    assert parse_record(type(parsed), to_dict(parsed), policy=NUMERIC_OFFLINE_V1) == parsed
    # Both input and returned JSON containers are detached, including nested data.
    for value in source.values():
        if type(value) in (list, dict):
            value.clear()
    source.clear()
    assert to_dict(parsed) == original
    exported = to_dict(parsed)
    for value in exported.values():
        if type(value) in (list, dict):
            value.clear()
    exported.clear()
    assert to_dict(parsed) == original


def test_complete_illustrative_catalog_parses_including_both_result_branches(illustrative_catalog):
    statuses = []
    for message in illustrative_catalog['messages']:
        parsed = parse_message(message, policy=NUMERIC_OFFLINE_V1)
        assert to_dict(parsed) == message
        assert loads_message(json.dumps(message), policy=NUMERIC_OFFLINE_V1) == parsed
        if message['message_type'] == 'ControlResult':
            statuses.append(parsed.payload.status)
    assert statuses == ['move', 'hold']
    for collection, name in (
        ('constraint_programs', 'ConstraintProgram'),
        ('execution_bindings', 'ProgramExecutionBinding'),
        ('validation_receipts', 'ProgramValidationReceipt'),
        ('command_acceptance_records', 'CommandAcceptanceRecord'),
    ):
        for value in illustrative_catalog[collection]:
            assert to_dict(parse(name, value)) == value


@pytest.mark.parametrize('name', LIVE_MESSAGES)
def test_five_live_payloads_are_supported_in_envelopes(live_values, records, name):
    message = {'message_type': name, 'meta': records['MessageMeta'], 'payload': live_values[name]}
    assert to_dict(parse_message(message, policy=NUMERIC_OFFLINE_V1)) == message
    message['payload']['unknown'] = True
    with pytest.raises(ValidationError) as caught:
        parse_message(message, policy=NUMERIC_OFFLINE_V1)
    assert caught.value.path == '$.payload.unknown'
    assert caught.value.code == 'unknown_field'


@pytest.mark.parametrize('name', ('TaskRef', 'ObjectWeight', 'ConstraintEvaluation', 'CommandAcceptanceRecord'))
def test_nested_values_and_application_logs_do_not_add_transport_message_types(live_values, records, name):
    message = {'message_type': name, 'meta': records['MessageMeta'], 'payload': live_values[name]}
    with pytest.raises(ValidationError) as caught:
        parse_message(message, policy=NUMERIC_OFFLINE_V1)
    assert caught.value.path == '$.message_type'
    assert caught.value.code == 'enum'


@pytest.mark.parametrize('name,key', RECORD_CASES)
def test_required_live_fields_are_not_implicitly_defaulted(live_values, name, key):
    for field in live_values[key]:
        value = deepcopy(live_values[key])
        del value[field]
        assert reject(name, value, f'$.{field}').code == 'missing_field'


@pytest.mark.parametrize('name,key', RECORD_CASES)
def test_live_unknown_fields_remain_strict(live_values, name, key):
    value = live_values[key]
    value['unexpected'] = None
    assert reject(name, value, '$.unexpected').code == 'unknown_field'


@pytest.mark.parametrize('name,key,field', [
    ('ControlSnapshot', 'ControlSnapshot', 'previous_applied_command_id'),
    ('ControlResult', 'hold', 'snapshot_id'),
    ('ControlResult', 'hold', 'weights_id'),
    ('ControlResult', 'hold', 'source_joystick_seq'),
    ('ControlResult', 'hold', 'source_robot_state_seq'),
    ('ControlResult', 'hold', 'constraints_satisfied'),
    ('CommandAcceptanceRecord', 'CommandAcceptanceRecord', 'applied_command_id'),
])
def test_nullable_live_fields_still_require_keys(live_values, name, key, field):
    value = live_values[key]
    value[field] = None
    parsed = parse(name, value)
    assert getattr(parsed, field) is None
    assert field in to_dict(parsed) and to_dict(parsed)[field] is None
    del value[field]
    assert reject(name, value, f'$.{field}').code == 'missing_field'


@pytest.mark.parametrize('name,path,bad', [
    ('JoystickSample', ('mapping_id',), ''),
    ('JoystickSample', ('u_xy',), [0]),
    ('JoystickSample', ('u_xy',), [0, 0, 0]),
    ('JoystickSample', ('u_xy',), (0, 0)),
    ('JoystickSample', ('u_xy', 0), True),
    ('JoystickSample', ('u_xy', 0), '0'),
    ('JoystickSample', ('enabled',), 1),
    ('RobotState', ('frame_id',), ''),
    ('RobotState', ('tcp_frame',), None),
    ('RobotState', ('joint_names', 0), ''),
    ('RobotState', ('joint_names', 0), 0),
    ('RobotState', ('joint_positions_rad', 0), False),
    ('RobotState', ('state_source',), 'commanded_setpoint'),
    ('RobotState', ('state_source',), 'synthetic'),
    ('ControlSnapshot', ('snapshot_seq',), -1),
    ('ControlSnapshot', ('snapshot_seq',), MAX_COUNTER + 1),
    ('ControlSnapshot', ('control_epoch',), True),
    ('ControlSnapshot', ('dt_s',), 0),
    ('ControlSnapshot', ('dt_s',), '0.02'),
    ('ControlSnapshot', ('joystick_history_refs', 0), ''),
    ('ControlSnapshot', ('task_ref', 'revision'), 0),
    ('ControlSnapshot', ('geometry_ref', 'revision'), 1.0),
    ('ControlSnapshot', ('previous_applied_command_id',), ''),
    ('TaskRef', ('revision',), MAX_COUNTER + 1),
    ('TaskRef', ('revision',), True),
    ('TaskRef', ('task_id',), ''),
    ('ObjectWeight', ('weight',), -0.1),
    ('ObjectWeight', ('weight',), True),
    ('ObjectWeights', ('assistance_strength',), -0.01),
    ('ObjectWeights', ('assistance_strength',), 1.01),
    ('ObjectWeights', ('assistance_strength',), False),
    ('ConstraintEvaluation', ('program_id',), ''),
    ('ConstraintEvaluation', ('raw_g',), '1'),
    ('ConstraintEvaluation', ('normalization_scale',), 0),
    ('ConstraintEvaluation', ('normalization_scale',), -1),
    ('ConstraintEvaluation', ('penalty',), -0.1),
    ('ConstraintEvaluation', ('penalty',), True),
    ('ControlResult', ('command_seq',), 1.0),
    ('ControlResult', ('source_joystick_seq',), MAX_COUNTER + 1),
    ('ControlResult', ('source_robot_state_seq',), False),
    ('ControlResult', ('status',), 'stop'),
    ('ControlResult', ('evaluation_valid',), 1),
    ('ControlResult', ('solver_status',), 'success'),
    ('ControlResult', ('constraints_satisfied',), 0),
    ('ControlResult', ('solve_time_s',), -0.01),
    ('ControlResult', ('horizon_s',), 0),
    ('CommandAcceptanceRecord', ('command_accepted',), 1),
    ('CommandAcceptanceRecord', ('checked_at', 'nanosec'), 1_000_000_000),
    ('CommandAcceptanceRecord', ('applied_command_id',), ''),
])
def test_live_bad_types_domains_and_dimensions_have_precise_paths(live_values, name, path, bad):
    value = live_values[name]
    if path == ('joystick_history_refs', 0):
        value['joystick_history_refs'] = ['sample']
    change(value, path, bad)
    expected_path = '$' + ''.join(f'[{key}]' if type(key) is int else f'.{key}' for key in path)
    reject(name, value, expected_path)


@pytest.mark.parametrize('name,path', [
    ('JoystickSample', ('u_xy', 0)),
    ('RobotState', ('tcp_pose', 'position_xyz', 0)),
    ('RobotState', ('joint_positions_rad', 0)),
    ('ControlSnapshot', ('dt_s',)),
    ('ObjectWeights', ('weights', 0, 'weight')),
    ('ObjectWeights', ('assistance_strength',)),
    ('ConstraintEvaluation', ('raw_g',)),
    ('ConstraintEvaluation', ('normalization_scale',)),
    ('ConstraintEvaluation', ('penalty',)),
    ('ControlResult', ('solve_time_s',)),
    ('ControlResult', ('horizon_s',)),
])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), 10**400])
def test_live_nonfinite_numbers_never_reach_consumers(live_values, name, path, bad):
    change(live_values[name], path, bad)
    assert reject(name, live_values[name]).code == 'non_finite'


@pytest.mark.parametrize('vector', [[0, 0], [1, 0], [-1, 0], [0, -1], [0.6, 0.8]])
def test_joystick_accepts_entire_closed_unit_disk_without_repair(live_values, vector):
    value = live_values['JoystickSample']
    value['u_xy'] = vector
    assert to_dict(parse('JoystickSample', value)) == value


@pytest.mark.parametrize('vector', [[1, 1], [1 + 1e-10, 0], [math.nextafter(1, math.inf), 0]])
def test_joystick_unit_disk_does_not_borrow_quaternion_tolerance(live_values, vector):
    value = live_values['JoystickSample']
    value['u_xy'] = vector
    reject('JoystickSample', value, '$.u_xy')


def test_disabled_joystick_preserves_sample_vector_without_inventing_stop_actions(live_values):
    value = live_values['JoystickSample']
    value['enabled'] = False
    assert to_dict(parse('JoystickSample', value)) == value


def test_optional_joint_velocity_is_absent_not_null_or_zero(live_values):
    value = live_values['RobotState']
    assert 'joint_velocities_radps' not in value
    parsed = parse('RobotState', value)
    assert parsed.joint_velocities_radps is None
    assert 'joint_velocities_radps' not in to_dict(parsed)
    value['joint_velocities_radps'] = None
    assert reject('RobotState', value, '$.joint_velocities_radps').code == 'type'
    value['joint_velocities_radps'] = [0, 0.1, -0.2, 0, 0, 0]
    parsed = parse('RobotState', value)
    assert parsed.joint_velocities_radps == (0, 0.1, -0.2, 0, 0, 0)
    assert type(parsed.joint_velocities_radps) is tuple
    assert to_dict(parsed) == value


@pytest.mark.parametrize('field', ['joint_positions_rad', 'joint_velocities_radps'])
def test_joint_measurement_arrays_must_match_name_count(live_values, field):
    value = live_values['RobotState']
    value[field] = [0] * (len(value['joint_names']) - 1)
    reject('RobotState', value, f'$.{field}')


def test_joint_names_are_unique_and_measured_provenance_is_explicit(live_values):
    value = live_values['RobotState']
    value['joint_names'][1] = value['joint_names'][0]
    assert reject('RobotState', value, '$.joint_names[1]').code == 'duplicate'
    value['joint_names'] = ['one-joint']
    value['joint_positions_rad'] = [0]
    value['state_source'] = 'fk_from_measured_joints'
    assert to_dict(parse('RobotState', value)) == value


@pytest.mark.parametrize('bad', [True, '0', float('nan'), float('inf'), 10**400])
def test_optional_joint_velocities_use_the_same_strict_number_boundary(live_values, bad):
    value = live_values['RobotState']
    value['joint_velocities_radps'] = [bad] + [0] * (len(value['joint_names']) - 1)
    reject('RobotState', value, '$.joint_velocities_radps[0]')


def test_object_weights_reject_empty_duplicate_and_negative_support(live_values):
    value = live_values['ObjectWeights']
    value['weights'] = []
    reject('ObjectWeights', value, '$.weights')
    value['weights'] = [{'object_id': 'same', 'weight': 0.5}] * 2
    assert reject('ObjectWeights', value, '$.weights[1].object_id').code == 'duplicate'
    value['weights'] = [{'object_id': 'a', 'weight': -0.1}, {'object_id': 'b', 'weight': 1.1}]
    reject('ObjectWeights', value, '$.weights[0].weight')


def test_weight_limits_allow_eight_candidates_and_reject_nine(live_values):
    value = live_values['ObjectWeights']
    value['weights'] = [{'object_id': f'o{i}', 'weight': 1 / 8} for i in range(8)]
    assert len(parse('ObjectWeights', value).weights) == 8
    value['weights'] = [{'object_id': f'o{i}', 'weight': 1 / 9} for i in range(9)]
    assert reject('ObjectWeights', value, '$.weights').code == 'limit'


def test_finite_individual_weights_cannot_overflow_the_sum(live_values):
    value = live_values['ObjectWeights']
    value['weights'] = [{'object_id': 'a', 'weight': 1e308}, {'object_id': 'b', 'weight': 1e308}]
    reject('ObjectWeights', value, '$.weights')


@pytest.mark.parametrize('offset,accepted', [(0, True), (0.5e-12, True), (-0.5e-12, True), (2e-12, False), (-2e-12, False)])
def test_weights_sum_uses_absolute_reference_tolerance_without_normalizing(live_values, offset, accepted):
    assert NUMERIC_OFFLINE_V1.weight_sum_tolerance == 1e-12
    value = live_values['ObjectWeights']
    value['weights'] = [{'object_id': 'a', 'weight': 0.5}, {'object_id': 'b', 'weight': 0.5 + offset}]
    if accepted:
        assert to_dict(parse('ObjectWeights', value)) == value
    else:
        reject('ObjectWeights', value, '$.weights')


def test_weight_sum_tolerance_is_explicit_host_policy_not_record_metadata(live_values):
    value = live_values['ObjectWeights']
    value['weights'] = [{'object_id': 'a', 'weight': 1 + 2e-12}]
    reject('ObjectWeights', value)
    looser = replace(NUMERIC_OFFLINE_V1, weight_sum_tolerance=3e-12)
    assert to_dict(parse('ObjectWeights', value, looser)) == value
    exact = replace(NUMERIC_OFFLINE_V1, weight_sum_tolerance=0)
    value['weights'][0]['weight'] = math.nextafter(1, 0)
    reject('ObjectWeights', value, policy=exact)


@pytest.mark.parametrize('bad', [-1, True, '0.001', float('nan'), float('inf'), 10**400])
def test_invalid_weight_sum_policy_is_rejected(bad):
    with pytest.raises(ValueError):
        replace(NUMERIC_OFFLINE_V1, weight_sum_tolerance=bad)


def test_existing_policy_constructor_remains_compatible():
    policy = ValidationPolicy('custom', 1e-8, 1e-8, 8192, 8, 32, 32, 32)
    assert policy.weight_sum_tolerance == 1e-12


@pytest.mark.parametrize('strength', [0, 1])
def test_zero_weight_and_assistance_endpoints_are_preserved(live_values, strength):
    value = live_values['ObjectWeights']
    value['weights'] = [{'object_id': 'a', 'weight': 0}, {'object_id': 'b', 'weight': 1}]
    value['assistance_strength'] = strength
    assert to_dict(parse('ObjectWeights', value)) == value


@pytest.mark.parametrize('field', ['snapshot_id', 'weights_id', 'source_joystick_seq', 'source_robot_state_seq', 'constraints_satisfied'])
def test_move_requires_nonnull_causal_fields_and_satisfaction_boolean(live_values, field):
    value = live_values['ControlResult']
    value[field] = None
    reject('ControlResult', value, f'$.{field}')


@pytest.mark.parametrize('field,bad', [
    ('evaluation_valid', False), ('solver_status', 'not_run'),
    ('solver_status', 'infeasible'), ('solver_status', 'timeout'),
    ('solver_status', 'error'), ('next_tcp_pose', None), ('horizon_s', None),
])
def test_move_cannot_be_constructed_from_failed_or_targetless_result(live_values, field, bad):
    value = live_values['ControlResult']
    value[field] = bad
    if bad == 'not_run':
        value['solve_time_s'] = 0
    reject('ControlResult', value, f'$.{field}')


@pytest.mark.parametrize('reason', [None, '', 'unrequested-hold-reason'])
def test_move_forbids_hold_reason_even_when_null(live_values, reason):
    value = live_values['ControlResult']
    value['reason'] = reason
    reject('ControlResult', value, '$.reason')


@pytest.mark.parametrize('field', ['next_tcp_pose', 'horizon_s'])
@pytest.mark.parametrize('null_value', [False, True])
def test_hold_forbids_motion_fields_even_when_null(live_values, field, null_value):
    value = live_values['hold']
    value[field] = None if null_value else live_values['ControlResult'][field]
    reject('ControlResult', value, f'$.{field}')


@pytest.mark.parametrize('reason', ['', ' \n\t', None, 1])
def test_hold_requires_nonblank_text_reason(live_values, reason):
    value = live_values['hold']
    value['reason'] = reason
    reject('ControlResult', value, '$.reason')


def test_not_run_has_zero_duration_but_other_hold_statuses_may_record_work(live_values):
    value = live_values['hold']
    value['solve_time_s'] = 0.01
    reject('ControlResult', value, '$.solve_time_s')
    for status in ('solved', 'infeasible', 'timeout', 'error'):
        value['solver_status'] = status
        assert to_dict(parse('ControlResult', value)) == value


@pytest.mark.parametrize('satisfied', [False, True])
def test_move_soft_relation_satisfaction_is_distinct_from_evaluation_success(live_values, satisfied):
    value = live_values['ControlResult']
    value['constraints_satisfied'] = satisfied
    parsed = parse('ControlResult', value)
    assert parsed.constraints_satisfied is satisfied
    assert parsed.reason is None and 'reason' not in to_dict(parsed)


@pytest.mark.parametrize('satisfied', [None, False, True])
def test_hold_can_preserve_partial_diagnostics_after_computation_failure(live_values, satisfied):
    value = live_values['hold']
    value.update(
        solver_status='error', solve_time_s=0.01, constraints_satisfied=satisfied,
        constraint_evaluations=[live_values['ConstraintEvaluation']],
    )
    parsed = parse('ControlResult', value)
    assert parsed.evaluation_valid is False
    assert parsed.next_tcp_pose is None and parsed.horizon_s is None
    assert to_dict(parsed) == value


@pytest.mark.parametrize('sequence', [0, MAX_COUNTER])
def test_live_counters_preserve_full_interoperable_range(live_values, sequence):
    snapshot = live_values['ControlSnapshot']
    snapshot.update(snapshot_seq=sequence, control_epoch=sequence)
    assert to_dict(parse('ControlSnapshot', snapshot)) == snapshot
    result = live_values['ControlResult']
    result.update(control_epoch=sequence, command_seq=sequence, source_joystick_seq=sequence, source_robot_state_seq=sequence)
    assert to_dict(parse('ControlResult', result)) == result


def test_application_log_is_independent_of_numerical_result_and_authority(live_values):
    move = parse('ControlResult', live_values['ControlResult'])
    receipt = parse('CommandAcceptanceRecord', live_values['CommandAcceptanceRecord'])
    assert move.evaluation_valid is True and move.solver_status == 'solved'
    assert receipt.command_accepted is False and receipt.applied_command_id is None
    log = live_values['CommandAcceptanceRecord']
    log.update(result_id='unresolved-result', command_accepted=True, applied_command_id='unresolved-command', reason='local_adapter_accepted')
    assert to_dict(parse('CommandAcceptanceRecord', log)) == log


def test_record_validation_does_not_resolve_context_freshness_or_acceptance(live_values, records):
    snapshot = live_values['ControlSnapshot']
    snapshot.update(config_id='unknown-config', semantic_id='unknown-semantic', joystick_ref='unknown-input', robot_state_ref='unknown-state', previous_applied_command_id='unproven-command')
    snapshot['task_ref'] = {'task_id': 'unknown-task', 'revision': 9}
    snapshot['geometry_ref'] = {'geometry_id': 'unknown-geometry', 'revision': 9}
    snapshot['program_refs'] = []
    snapshot['execution_binding_refs'] = []
    snapshot['joystick_history_refs'] = ['later-sample', 'earlier-sample']
    assert to_dict(parse('ControlSnapshot', snapshot)) == snapshot
    weights = live_values['ObjectWeights']
    weights['weights'] = [{'object_id': 'outside-semantic-candidates', 'weight': 1}]
    weights['snapshot_id'] = 'other-snapshot'
    assert to_dict(parse('ObjectWeights', weights)) == weights
    result = live_values['ControlResult']
    result['horizon_s'] = snapshot['dt_s'] * 2
    assert to_dict(parse('ControlResult', result)) == result
    state = live_values['RobotState']
    state.update(frame_id='other-control-frame', tcp_frame='other-tcp')
    state['sampled_at'] = {'clock_id': 'unrelated-old-clock', 'sec': -100, 'nanosec': 0}
    message = {'message_type': 'RobotState', 'meta': records['MessageMeta'], 'payload': state}
    assert to_dict(parse_message(message, policy=NUMERIC_OFFLINE_V1)) == message


def test_constraint_evaluation_does_not_replay_or_certify_reported_penalty(live_values):
    value = live_values['ConstraintEvaluation']
    value.update(raw_g=-1, normalization_scale=2, penalty=123)
    assert to_dict(parse('ConstraintEvaluation', value)) == value


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_serializing_live_internal_nonfinite_value_still_fails(live_values, bad):
    evaluation = replace(parse('ConstraintEvaluation', live_values['ConstraintEvaluation']), penalty=bad)
    with pytest.raises(ValidationError) as caught:
        to_dict(evaluation)
    assert caught.value.path == '$.penalty'


def test_live_json_loader_keeps_duplicate_key_and_nonfinite_rejections(live_values, records):
    message = {'message_type': 'JoystickSample', 'meta': records['MessageMeta'], 'payload': live_values['JoystickSample']}
    raw = json.dumps(message)
    duplicate = raw.replace('"enabled": true', '"enabled": true, "enabled": false')
    with pytest.raises(ValidationError) as caught:
        loads_message(duplicate, policy=NUMERIC_OFFLINE_V1)
    assert caught.value.code == 'duplicate_key'
    assert caught.value.path == '$.payload.enabled'
    for token in ('NaN', 'Infinity', '-Infinity'):
        nonfinite = raw.replace('0.4', token)
        with pytest.raises(ValidationError) as caught:
            loads_message(nonfinite, policy=NUMERIC_OFFLINE_V1)
        assert caught.value.code == 'non_finite'
        assert caught.value.path == '$.payload.u_xy[0]'
