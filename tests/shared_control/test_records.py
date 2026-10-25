"""Behavioral evidence for schema 1.0 scene/artifact parsing (S01–S05 subset)."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, is_dataclass, replace
import json
from pathlib import Path
import re

import pytest

import ssc_core
from ssc_core import (
    NUMERIC_OFFLINE_V1,
    ValidationError,
    loads_message,
    parse_message,
    parse_record,
    to_dict,
)


RECORD_TYPES = (
    'SessionConfig', 'TaskContext', 'SceneObservation', 'SceneGeometry',
    'SemanticContext', 'ConstraintProgram', 'Parameter',
    'ProgramExecutionBinding', 'ProgramValidationReceipt', 'TimePoint', 'Pose',
    'StampedTransform', 'AssetRef', 'ProgramRef', 'GeometryRef', 'MessageMeta',
    'Object', 'Keypoint', 'Axis', 'ObjectRelevance',
)
TOP_LEVEL_TYPES = RECORD_TYPES[:9]
MAX_COUNTER = 2**53 - 1


def parse(name, value):
    return parse_record(name, value, policy=NUMERIC_OFFLINE_V1)


def change(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def reject(name, value, token=None):
    with pytest.raises(ValidationError) as caught:
        parse(name, value)
    error = caught.value
    assert error.path.startswith('$')
    assert isinstance(error.code, str) and error.code
    assert isinstance(error.message, str) and error.message
    if token is not None:
        assert token in error.path, str(error)
    return error


@pytest.mark.parametrize('name', RECORD_TYPES)
def test_published_records_round_trip_as_exported_frozen_types(records, name):
    record = parse(name, records[name])
    assert isinstance(record, getattr(ssc_core, name))
    assert is_dataclass(record)
    assert to_dict(record) == records[name]
    assert parse_record(type(record), to_dict(record), policy=NUMERIC_OFFLINE_V1) == record
    field = next(iter(records[name]))
    with pytest.raises(FrozenInstanceError):
        setattr(record, field, None)


def test_illustrative_catalog_is_preserved_verbatim(illustrative_catalog):
    root = Path(__file__).resolve().parents[2]
    document = (root / 'docs/wiki/Data-Model-and-Message-Contracts.md').read_text()
    block = re.findall(r'```json\n(.*?)\n```', document, re.S)[0]
    fixture = root / 'tests/fixtures/shared_control/bottle_cup_illustrative.json'
    assert fixture.read_text() == block + '\n'
    assert len(illustrative_catalog['messages']) == 12
    assert any(m['message_type'] == 'ControlResult' for m in illustrative_catalog['messages'])


@pytest.mark.parametrize('catalog_name', ['illustrative_catalog', 'offline_catalog'])
def test_in_scope_catalog_messages_and_artifacts(request, catalog_name):
    catalog = request.getfixturevalue(catalog_name)
    for message in catalog['messages']:
        if message['message_type'] in TOP_LEVEL_TYPES:
            parsed = parse_message(message, policy=NUMERIC_OFFLINE_V1)
            assert to_dict(parsed) == message
            assert loads_message(json.dumps(message), policy=NUMERIC_OFFLINE_V1) == parsed
    for key, name in (
        ('constraint_programs', 'ConstraintProgram'),
        ('execution_bindings', 'ProgramExecutionBinding'),
        ('validation_receipts', 'ProgramValidationReceipt'),
    ):
        for value in catalog[key]:
            assert to_dict(parse(name, value)) == value


def test_offline_fixture_uses_reference_values_without_issuing_receipts(offline_catalog):
    fixture = offline_catalog
    messages = {m['message_type']: m['payload'] for m in fixture['messages']}
    assert fixture['fixture_kind'] == 'synthetic_structure_only'
    assert fixture['profile_id'] == 'offline-reference-v1'
    assert fixture['validation_receipts'] == []
    config = messages['SessionConfig']
    assert (config['config_id'], config['validation_policy_id']) == ('config-offline-v1', 'numeric-offline-v1')
    assert (config['control_frame'], config['tcp_frame']) == ('synthetic_base', 'synthetic_tcp')
    assert config['robot_model_id'] == 'synthetic-rotary-v1'
    assert config['motion_constraint_set_id'] == 'synthetic-host-v1'
    expected = {
        'control_period_s': .2, 'max_xy_speed_mps': .05, 'max_linear_speed_mps': .05,
        'max_angular_speed_radps': .5, 'joystick_timeout_s': .5,
        'robot_state_timeout_s': .5, 'solve_budget_s': .15, 'max_command_age_s': .2,
        'history_window_s': 1, 'max_capture_skew_s': .01,
    }
    assert {key: config[key] for key in expected} == expected
    assert fixture['initial_pose']['position_xyz'] == [.4, 0, .3]
    assert [kp['position_xyz'] for kp in messages['SceneGeometry']['keypoints']] == [[.41, 0, .3], [.39, 0, .3]]
    assert [item['task_relevance'] for item in messages['SemanticContext']['object_relevance']] == [.5, .5]
    assert all(p['parameters'][0]['value'] == 0 for p in fixture['constraint_programs'])
    assert all(b['normalization_scale'] == .01 and b['weight'] == 1 for b in fixture['execution_bindings'])
    assert all(b['validation_receipt_id'].startswith('unissued-') for b in fixture['execution_bindings'])


@pytest.mark.parametrize('name', RECORD_TYPES)
def test_required_fields_are_not_replaced_or_defaulted(records, name):
    for field in records[name]:
        missing = deepcopy(records[name])
        del missing[field]
        assert reject(name, missing, field).code == 'missing_field'


@pytest.mark.parametrize('name', RECORD_TYPES)
def test_unknown_fields_are_rejected(records, name):
    value = records[name]
    value['unexpected_field'] = 0
    assert reject(name, value, 'unexpected_field').code == 'unknown_field'


@pytest.mark.parametrize('name', TOP_LEVEL_TYPES)
def test_every_supported_record_can_be_enveloped(records, name):
    value = {'message_type': name, 'meta': records['MessageMeta'], 'payload': records[name]}
    parsed = parse_message(value, policy=NUMERIC_OFFLINE_V1)
    assert parsed.message_type == name
    assert to_dict(parsed) == value


@pytest.mark.parametrize('value', [None, [], 0, True, 'record'])
def test_non_object_records_are_rejected(value):
    reject('TaskContext', value)


@pytest.mark.parametrize('field', ['message_type', 'meta', 'payload'])
def test_envelope_requires_each_field(records, field):
    value = {'message_type': 'TaskContext', 'meta': records['MessageMeta'], 'payload': records['TaskContext']}
    del value[field]
    with pytest.raises(ValidationError) as error:
        parse_message(value, policy=NUMERIC_OFFLINE_V1)
    assert field in error.value.path


def test_explicit_policy_is_required(records):
    with pytest.raises(TypeError):
        parse_record('TaskContext', records['TaskContext'])
    with pytest.raises(TypeError):
        parse_message({})
    with pytest.raises(TypeError):
        loads_message('{}')


@pytest.mark.parametrize('name,path,bad', [
    ('TaskContext', ('task_id',), ''),
    ('TaskContext', ('instruction',), ' \n\t'),
    ('TaskContext', ('revision',), 0),
    ('TaskContext', ('revision',), 1.0),
    ('TaskContext', ('revision',), True),
    ('MessageMeta', ('schema_version',), '2.0'),
    ('MessageMeta', ('sequence',), -1),
    ('MessageMeta', ('sequence',), MAX_COUNTER + 1),
    ('MessageMeta', ('sequence',), '1'),
    ('MessageMeta', ('sequence',), False),
    ('TimePoint', ('sec',), 10.0),
    ('TimePoint', ('sec',), True),
    ('TimePoint', ('nanosec',), -1),
    ('TimePoint', ('nanosec',), 1_000_000_000),
    ('TimePoint', ('nanosec',), False),
    ('TimePoint', ('clock_id',), ''),
    ('GeometryRef', ('revision',), MAX_COUNTER + 1),
    ('Pose', ('position_xyz',), [1, 2]),
    ('Pose', ('position_xyz',), [1, 2, 3, 4]),
    ('Pose', ('position_xyz',), [1, '2', 3]),
    ('Pose', ('quaternion_xyzw',), [0, 0, 1]),
    ('Pose', ('quaternion_xyzw',), [0, 0, 0, 0]),
    ('Pose', ('quaternion_xyzw',), [0, 0, 0, 1.0000001]),
    ('Pose', ('quaternion_xyzw',), [0, 0, 0, 1e308]),
    ('AssetRef', ('shape',), [0, 640, 3]),
    ('AssetRef', ('shape',), [480, -1, 3]),
    ('AssetRef', ('shape',), [480.0, 640, 3]),
    ('AssetRef', ('shape',), [True, 640, 3]),
    ('ProgramRef', ('source_sha256',), 'A' * 64),
    ('ProgramRef', ('source_sha256',), 'a' * 63),
    ('ProgramRef', ('source_sha256',), 'g' * 64),
    ('SceneGeometry', ('mode',), 'dynamic'),
    ('SceneGeometry', ('revision',), 0),
    ('SceneGeometry', ('objects',), []),
    ('SceneGeometry', ('keypoints',), []),
    ('SceneGeometry', ('axes', 0, 'unit_direction_xyz'), [0, 0, 0]),
    ('SceneGeometry', ('axes', 0, 'unit_direction_xyz'), [0, 0, 2]),
    ('SceneGeometry', ('axes', 0, 'unit_direction_xyz'), [0, 1]),
    ('SceneGeometry', ('axes', 0, 'directed'), 0),
    ('SemanticContext', ('object_relevance',), []),
    ('SemanticContext', ('object_relevance', 0, 'task_relevance'), -.01),
    ('SemanticContext', ('object_relevance', 0, 'task_relevance'), 1.01),
    ('SemanticContext', ('object_relevance', 0, 'task_relevance'), True),
    ('ConstraintProgram', ('function_api_version',), 'geometry-residual-v2'),
    ('ConstraintProgram', ('entrypoint',), 'exec'),
    ('ConstraintProgram', ('output_unit',), 'cm'),
    ('ConstraintProgram', ('source_sha256',), 'A' * 64),
    ('Parameter', ('unit',), 'degrees'),
    ('Parameter', ('value',), '0.02'),
    ('Parameter', ('value',), False),
    ('Parameter', ('name',), ''),
    ('ProgramExecutionBinding', ('normalization_scale',), 0),
    ('ProgramExecutionBinding', ('normalization_scale',), -1),
    ('ProgramExecutionBinding', ('weight',), -.1),
    ('ProgramExecutionBinding', ('weight',), True),
    ('ProgramValidationReceipt', ('status',), 'approved'),
    ('ProgramValidationReceipt', ('function_api_version',), 'geometry-residual-v0'),
    ('ProgramValidationReceipt', ('validation_geometry_revision',), 0),
    ('ProgramValidationReceipt', ('diagnostics',), ['']),
    ('ProgramValidationReceipt', ('diagnostics',), [' \t']),
])
def test_structural_and_numeric_rejections_have_field_diagnostics(records, name, path, bad):
    value = records[name]
    change(value, path, bad)
    reject(name, value, str(path[-1]))


@pytest.mark.parametrize('field', [
    'control_period_s', 'max_xy_speed_mps', 'max_linear_speed_mps',
    'max_angular_speed_radps', 'joystick_timeout_s', 'robot_state_timeout_s',
    'solve_budget_s', 'max_command_age_s', 'history_window_s',
])
@pytest.mark.parametrize('bad', [0, -1, '0.1', True])
def test_configuration_positive_numeric_fields(records, field, bad):
    records['SessionConfig'][field] = bad
    reject('SessionConfig', records['SessionConfig'], field)


@pytest.mark.parametrize('name,path', [
    ('SessionConfig', ('max_capture_skew_s',)),
    ('Pose', ('position_xyz', 0)),
    ('Pose', ('quaternion_xyzw', 3)),
    ('SceneObservation', ('K', 0, 0)),
    ('SceneGeometry', ('keypoints', 0, 'position_xyz', 1)),
    ('SceneGeometry', ('axes', 0, 'unit_direction_xyz', 2)),
    ('SemanticContext', ('object_relevance', 0, 'task_relevance')),
    ('Parameter', ('value',)),
    ('ProgramExecutionBinding', ('weight',)),
    ('ProgramExecutionBinding', ('normalization_scale',)),
])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_internal_nonfinite_numbers_fail_before_use(records, name, path, bad):
    change(records[name], path, bad)
    assert reject(name, records[name]).code == 'non_finite'


@pytest.mark.parametrize('path,bad', [
    (('K',), [[600, 0, 300], [0, 600, 200]]),
    (('K',), [[600, 0], [0, 600], [0, 0]]),
    (('K', 0, 0), 0),
    (('K', 1, 1), -600),
    (('projection_model',), 'fisheye'),
    (('rgb_ref', 'encoding'), 'bgr8'),
    (('depth_ref', 'encoding'), 'uint16_mm'),
    (('rgb_ref', 'shape'), [480, 640, 4]),
    (('depth_ref', 'shape'), [480, 640, 1]),
    (('depth_ref', 'shape'), [240, 320]),
    (('depth_stamp', 'clock_id'), 'unrelated-epoch'),
    (('T_control_camera', 'child_frame'), 'different-camera'),
    (('T_control_camera', 'sampled_at', 'sec'), 11),
    (('T_control_camera', 'sampled_at', 'nanosec'), 1),
    (('T_control_camera', 'sampled_at', 'clock_id'), 'other-clock'),
])
def test_observation_local_shape_capture_and_camera_consistency(records, path, bad):
    change(records['SceneObservation'], path, bad)
    reject('SceneObservation', records['SceneObservation'])


@pytest.mark.parametrize('collection,id_field', [
    ('objects', 'object_id'), ('keypoints', 'keypoint_id'), ('axes', 'axis_id'),
])
def test_geometry_ids_are_unique_within_each_namespace(records, collection, id_field):
    geometry = records['SceneGeometry']
    geometry[collection][1][id_field] = geometry[collection][0][id_field]
    assert reject('SceneGeometry', geometry, id_field).code == 'duplicate'


@pytest.mark.parametrize('path,bad', [
    (('keypoints', 0, 'object_id'), 'missing-object'),
    (('axes', 0, 'object_id'), 'missing-object'),
    (('axes', 0, 'origin_keypoint_id'), 'missing-origin'),
    (('axes', 0, 'origin_keypoint_id'), 'kp-cup-origin'),
])
def test_geometry_ownership_and_axis_origin_must_resolve_locally(records, path, bad):
    change(records['SceneGeometry'], path, bad)
    assert reject('SceneGeometry', records['SceneGeometry']).code == 'reference'


@pytest.mark.parametrize('field', ['keypoint_bindings', 'axis_bindings'])
def test_program_bindings_are_ordered_unique_ids(records, field):
    program = records['ConstraintProgram']
    program[field] = ['feature-a', 'feature-a']
    assert reject('ConstraintProgram', program, field).code == 'duplicate'


def test_parameter_names_and_semantic_object_ids_are_unique(records):
    program = records['ConstraintProgram']
    program['parameters'].append(deepcopy(program['parameters'][0]))
    assert reject('ConstraintProgram', program, 'parameters').code == 'duplicate'
    semantic = records['SemanticContext']
    semantic['object_relevance'][1]['object_id'] = semantic['object_relevance'][0]['object_id']
    assert reject('SemanticContext', semantic, 'object_relevance').code == 'duplicate'


@pytest.mark.parametrize('field', ['keypoint_bindings', 'axis_bindings', 'parameters'])
def test_program_count_limits_accept_32_reject_33(records, field):
    program = records['ConstraintProgram']
    values = [({'name': f'p{i}', 'value': i, 'unit': 'm'} if field == 'parameters' else f'feature-{i}') for i in range(33)]
    program[field] = values[:32]
    assert len(getattr(parse('ConstraintProgram', program), field)) == 32
    program[field] = values
    assert reject('ConstraintProgram', program, field).code == 'limit'


def test_candidate_limit_accepts_eight_rejects_nine(records):
    semantic = records['SemanticContext']
    values = [{'object_id': f'object-{i}', 'task_relevance': .5} for i in range(9)]
    semantic['object_relevance'] = values[:8]
    assert len(parse('SemanticContext', semantic).object_relevance) == 8
    semantic['object_relevance'] = values
    assert reject('SemanticContext', semantic, 'object_relevance').code == 'limit'


@pytest.mark.parametrize('source', ['x' * 8192, 'é' * 4096])
def test_source_limit_counts_utf8_bytes_and_does_not_interpret_source(records, source):
    program = records['ConstraintProgram']
    program['source_code'] = source
    assert parse('ConstraintProgram', program).source_code == source
    program['source_code'] += 'x'
    assert reject('ConstraintProgram', program, 'source_code').code == 'limit'


def test_source_bytes_and_declared_hash_are_preserved_without_execution(records):
    program = records['ConstraintProgram']
    source = 'not executable python\r\n# café\n'
    program['source_code'] = source
    program['source_sha256'] = '0' * 64
    parsed = parse('ConstraintProgram', program)
    assert parsed.source_code.encode('utf-8') == source.encode('utf-8')
    assert parsed.source_sha256 == '0' * 64


@pytest.mark.parametrize('sec', [-1, -(2**100), 2**100])
def test_timestamp_seconds_are_signed_integers_not_json_counters(records, sec):
    records['TimePoint']['sec'] = sec
    assert parse('TimePoint', records['TimePoint']).sec == sec


@pytest.mark.parametrize('nanosec', [0, 999_999_999])
def test_nanosecond_closed_lower_open_upper_bounds(records, nanosec):
    records['TimePoint']['nanosec'] = nanosec
    assert parse('TimePoint', records['TimePoint']).nanosec == nanosec


@pytest.mark.parametrize('sequence', [0, MAX_COUNTER])
def test_json_counter_boundaries(records, sequence):
    records['MessageMeta']['sequence'] = sequence
    assert parse('MessageMeta', records['MessageMeta']).sequence == sequence


def test_legal_empty_bindings_zero_weight_and_unnormalized_relevance(records):
    program = records['ConstraintProgram']
    for field in ('keypoint_bindings', 'axis_bindings', 'parameters'):
        program[field] = []
    assert parse('ConstraintProgram', program).parameters == ()
    binding = records['ProgramExecutionBinding']
    binding['weight'] = 0
    assert parse('ProgramExecutionBinding', binding).weight == 0
    semantic = records['SemanticContext']
    assert sum(r['task_relevance'] for r in semantic['object_relevance']) != 1
    assert to_dict(parse('SemanticContext', semantic)) == semantic
    for item in semantic['object_relevance']:
        item['task_relevance'] = 0
    parse('SemanticContext', semantic)
    records['SessionConfig']['max_capture_skew_s'] = 0
    parse('SessionConfig', records['SessionConfig'])


def test_point_only_geometry_and_objects_without_own_features_are_legal(records):
    geometry = records['SceneGeometry']
    geometry['axes'] = []
    geometry['keypoints'] = geometry['keypoints'][:1]
    parsed = parse('SceneGeometry', geometry)
    assert parsed.axes == ()
    assert len(parsed.objects) == 2


@pytest.mark.parametrize('name,path', [
    ('Pose', ('quaternion_xyzw',)),
    ('SceneGeometry', ('axes', 0, 'unit_direction_xyz')),
])
def test_norm_tolerance_accepts_without_silent_normalization(records, name, path):
    vector = [0, 0, 0, 1 + 5e-9] if name == 'Pose' else [0, 0, 1 + 5e-9]
    change(records[name], path, vector)
    assert to_dict(parse(name, records[name])) == records[name]


def test_local_parser_does_not_resolve_cross_artifact_ids_or_policy_metadata(records):
    for name, field in (
        ('SessionConfig', 'validation_policy_id'), ('ProgramExecutionBinding', 'validation_receipt_id'),
        ('ProgramValidationReceipt', 'validator_policy_version'), ('ConstraintProgram', 'object_id'),
    ):
        records[name][field] = 'unresolved-external-test-identity'
        assert getattr(parse(name, records[name]), field) == 'unresolved-external-test-identity'
    observation = records['SceneObservation']
    observation['T_control_camera']['parent_frame'] = 'unresolved-control-frame'
    observation['depth_stamp']['sec'] += 100
    parse('SceneObservation', observation)  # Capture-skew/config compatibility are a later boundary.


@pytest.mark.parametrize('diagnostics', [[], [''], [' \n']])
def test_rejected_receipts_require_nonblank_diagnostics(records, diagnostics):
    receipt = records['ProgramValidationReceipt']
    receipt.update(status='rejected', diagnostics=diagnostics)
    reject('ProgramValidationReceipt', receipt, 'diagnostics')


def test_receipt_status_is_data_not_execution_authority(records):
    receipt = records['ProgramValidationReceipt']
    receipt.update(status='rejected', diagnostics=['Unknown feature binding.'])
    assert parse('ProgramValidationReceipt', receipt).diagnostics == ('Unknown feature binding.',)
    receipt.update(status='accepted', diagnostics=[])
    assert parse('ProgramValidationReceipt', receipt).status == 'accepted'
    # Parsing does not issue/verify a receipt or resolve any of its identities.


def test_nested_output_is_immutable_and_detached_in_both_directions(records):
    original = deepcopy(records['SceneGeometry'])
    parsed = parse('SceneGeometry', records['SceneGeometry'])
    records['SceneGeometry']['keypoints'][0]['position_xyz'][0] = 100
    records['SceneGeometry']['objects'].clear()
    assert to_dict(parsed) == original
    assert isinstance(parsed.keypoints, tuple)
    assert isinstance(parsed.keypoints[0].position_xyz, tuple)
    with pytest.raises(TypeError):
        parsed.keypoints[0].position_xyz[0] = 100
    with pytest.raises(FrozenInstanceError):
        parsed.keypoints[0].object_id = 'foreign'
    serialized = to_dict(parsed)
    serialized['keypoints'][0]['position_xyz'][0] = -100
    assert to_dict(parsed) == original


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_serialization_rejects_nonfinite_internally_created_values(records, bad):
    parameter = replace(parse('Parameter', records['Parameter']), value=bad)
    with pytest.raises(ValidationError):
        to_dict(parameter)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity'])
def test_nonstandard_json_numbers_are_rejected_at_load(records, token):
    message = {'message_type': 'Parameter', 'meta': records['MessageMeta'], 'payload': records['Parameter']}
    raw = json.dumps(message).replace('0.02', token)
    with pytest.raises(ValidationError):
        loads_message(raw, policy=NUMERIC_OFFLINE_V1)


@pytest.mark.parametrize('raw', [
    '{"message_type":"TaskContext","message_type":"Parameter","meta":{},"payload":{}}',
    '{"message_type":"TaskContext","meta":{},"payload":{"task_id":"a","task_id":"b"}}',
])
def test_duplicate_json_keys_are_rejected_before_overwriting(raw):
    with pytest.raises(ValidationError) as error:
        loads_message(raw, policy=NUMERIC_OFFLINE_V1)
    assert error.value.code == 'duplicate_key'


@pytest.mark.parametrize('raw', ['{', '{"message_type":}', '[]', 'null'])
def test_malformed_or_nonobject_json_has_validation_error(raw):
    with pytest.raises(ValidationError):
        loads_message(raw, policy=NUMERIC_OFFLINE_V1)


def test_unknown_message_type_extra_envelope_keys_and_nested_errors(records):
    message = {'message_type': 'FutureType', 'meta': records['MessageMeta'], 'payload': {}}
    with pytest.raises(ValidationError):
        parse_message(message, policy=NUMERIC_OFFLINE_V1)
    message.update(message_type='TaskContext', payload=records['TaskContext'], extra=True)
    with pytest.raises(ValidationError) as error:
        parse_message(message, policy=NUMERIC_OFFLINE_V1)
    assert 'extra' in error.value.path
    del message['extra']
    message['payload']['revision'] = False
    with pytest.raises(ValidationError) as error:
        parse_message(message, policy=NUMERIC_OFFLINE_V1)
    assert error.value.path == '$.payload.revision'


def test_feature_storage_order_and_separate_id_namespaces_are_not_semantic_order(records):
    geometry = records['SceneGeometry']
    geometry['keypoints'].reverse()
    geometry['objects'].reverse()
    geometry['axes'].reverse()
    # IDs are unique within namespaces, not globally across record kinds.
    geometry['axes'][0]['axis_id'] = geometry['objects'][0]['object_id']
    assert to_dict(parse('SceneGeometry', geometry)) == geometry


def test_binding_order_is_preserved_and_unresolved_references_are_structurally_legal(records):
    program = records['ConstraintProgram']
    program['keypoint_bindings'] = ['second-feature', 'first-feature']
    program['axis_bindings'] = ['axis-two', 'axis-one']
    parsed = parse('ConstraintProgram', program)
    assert parsed.keypoint_bindings == ('second-feature', 'first-feature')
    assert parsed.axis_bindings == ('axis-two', 'axis-one')
    records['SemanticContext']['program_refs'] = []
    assert parse('SemanticContext', records['SemanticContext']).program_refs == ()


def test_utf8_source_cannot_contain_an_unpaired_surrogate(records):
    records['ConstraintProgram']['source_code'] = '\ud800'
    reject('ConstraintProgram', records['ConstraintProgram'], 'source_code')
