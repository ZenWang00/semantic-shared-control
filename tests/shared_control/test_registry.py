"""Immutable graph resolution evidence, using synthetic compatibility receipts.

The receipts constructed here exercise reference compatibility only. They are
not validator output, do not execute generated source, and grant no authority.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from ssc_core import (
    NUMERIC_OFFLINE_V1,
    GeometryRef,
    ProgramRef,
    RegistryError,
    SessionRegistry,
    TaskContext,
    TaskRef,
    ValidationError,
    parse_message,
    parse_record,
    to_dict,
)


SESSION = 'session-offline-v1'
POLICY = NUMERIC_OFFLINE_V1


def envelope(kind, payload, identifier, *, sequence=100):
    return {
        'message_type': kind,
        'meta': {
            'schema_version': '1.0', 'session_id': SESSION,
            'message_id': identifier, 'source_id': 'registry-unit-test',
            'sequence': sequence,
            'produced_at': {'clock_id': 'synthetic-clock-offline-v1', 'sec': 10, 'nanosec': 0},
        },
        'payload': payload,
    }


@pytest.fixture
def graph(offline_catalog):
    catalog = deepcopy(offline_catalog)
    messages = {m['message_type']: m for m in catalog['messages']}
    payloads = {kind: message['payload'] for kind, message in messages.items()}
    geometry = payloads['SceneGeometry']
    semantic = payloads['SemanticContext']
    task = payloads['TaskContext']
    config = payloads['SessionConfig']
    programs = catalog['constraint_programs']
    bindings = catalog['execution_bindings']
    receipts = []
    for index, (program, binding) in enumerate(zip(programs, bindings)):
        binding['validation_receipt_id'] = f'synthetic-test-receipt-{index}'
        receipts.append({
            'receipt_id': binding['validation_receipt_id'],
            'program_id': program['program_id'],
            'source_sha256': program['source_sha256'],
            'function_api_version': program['function_api_version'],
            'scene_id': geometry['scene_id'],
            'feature_set_id': geometry['feature_set_id'],
            'validation_geometry_id': geometry['geometry_id'],
            'validation_geometry_revision': geometry['revision'],
            'validator_policy_version': config['validation_policy_id'],
            'status': 'accepted', 'diagnostics': [],
        })
    stamp = {'clock_id': 'synthetic-clock-offline-v1', 'sec': 10, 'nanosec': 0}
    joystick = {
        'sampled_at': deepcopy(stamp), 'mapping_id': 'synthetic-test-mapping',
        'u_xy': catalog['current_input_xy'], 'enabled': True,
    }
    history = deepcopy(joystick)
    history['sampled_at'] = catalog['earlier_input']['sampled_at']
    state = {
        'sampled_at': deepcopy(stamp), 'frame_id': config['control_frame'],
        'tcp_frame': config['tcp_frame'], 'tcp_pose': catalog['initial_pose'],
        'joint_names': catalog['initial_joint_names'],
        'joint_positions_rad': catalog['initial_joint_positions_rad'],
        'state_source': 'measured_feedback',
    }
    messages['JoystickSample'] = envelope('JoystickSample', joystick, 'joystick-current')
    messages['JoystickHistory'] = envelope('JoystickSample', history, 'joystick-earlier', sequence=99)
    messages['RobotState'] = envelope('RobotState', state, 'robot-state-current')
    snapshot = {
        'snapshot_id': 'snapshot-test', 'snapshot_seq': 1, 'control_epoch': 0,
        'config_id': config['config_id'],
        'task_ref': {'task_id': task['task_id'], 'revision': task['revision']},
        'dt_s': catalog['dt_s'], 'joystick_ref': 'joystick-current',
        'robot_state_ref': 'robot-state-current',
        'joystick_history_refs': ['joystick-earlier'],
        'geometry_ref': {'geometry_id': geometry['geometry_id'], 'revision': geometry['revision']},
        'semantic_id': semantic['semantic_id'],
        'program_refs': deepcopy(semantic['program_refs']),
        'execution_binding_refs': [binding['binding_id'] for binding in bindings],
        'previous_applied_command_id': None,
    }
    weights = {
        'weights_id': 'weights-test', 'snapshot_id': snapshot['snapshot_id'],
        'estimator_config_id': 'synthetic-test-estimator',
        'weights': [
            {'object_id': item['object_id'], 'weight': .5}
            for item in semantic['object_relevance']
        ],
        'assistance_strength': .5,
    }
    messages['ControlSnapshot'] = envelope('ControlSnapshot', snapshot, 'message-snapshot')
    messages['ObjectWeights'] = envelope('ObjectWeights', weights, 'message-weights')
    return {
        **payloads, 'messages': messages, 'programs': programs,
        'bindings': bindings, 'receipts': receipts,
        'ControlSnapshot': snapshot, 'ObjectWeights': weights,
    }


def build(graph):
    registry = SessionRegistry(SESSION, policy=POLICY)
    for message in graph['messages'].values():
        registry.register_message(message)
    for name, key in (
        ('ConstraintProgram', 'programs'),
        ('ProgramExecutionBinding', 'bindings'),
        ('ProgramValidationReceipt', 'receipts'),
    ):
        for value in graph[key]:
            registry.register_record(name, value, session_id=SESSION)
    return registry


def bundle_args(graph):
    snapshot = graph['ControlSnapshot']
    return {key: deepcopy(snapshot[key]) for key in (
        'config_id', 'task_ref', 'geometry_ref', 'semantic_id',
        'program_refs', 'execution_binding_refs',
    )}


def assert_error(operation, code=None):
    with pytest.raises(RegistryError) as caught:
        operation()
    error = caught.value
    assert error.path.startswith('$')
    assert error.code and error.message
    if code:
        assert error.code == code, str(error)
    return error


def test_complete_graph_resolves_exact_immutable_inputs(graph):
    registry = build(graph)
    result = registry.resolve_weights('weights-test')
    snapshot = result.snapshot
    bundle = snapshot.bundle
    assert bundle.session_id == SESSION
    assert to_dict(bundle.configuration) == graph['SessionConfig']
    assert to_dict(bundle.task) == graph['TaskContext']
    assert to_dict(bundle.geometry) == graph['SceneGeometry']
    assert to_dict(bundle.semantic) == graph['SemanticContext']
    assert to_dict(snapshot.snapshot) == graph['ControlSnapshot']
    assert snapshot.joystick.meta.message_id == 'joystick-current'
    assert snapshot.robot_state.meta.message_id == 'robot-state-current'
    assert [item.meta.message_id for item in snapshot.joystick_history] == ['joystick-earlier']
    assert {item.program.program_id for item in bundle.programs} == {
        item['program_id'] for item in graph['programs']
    }
    assert {item.binding_id for item in bundle.execution_bindings} == {
        item['binding_id'] for item in graph['bindings']
    }
    assert {item.receipt_id for item in bundle.receipts} == {
        item['receipt_id'] for item in graph['receipts']
    }
    assert [candidate.object_id for candidate in bundle.candidates] == [
        item['object_id'] for item in graph['SemanticContext']['object_relevance']
    ]
    assert [weight.object_id for weight in result.candidate_weights] == [
        candidate.object_id for candidate in bundle.candidates
    ]
    assert to_dict(result.weights) == graph['ObjectWeights']
    with pytest.raises(FrozenInstanceError):
        bundle.session_id = 'changed'


def test_idempotent_registration_accepts_records_and_message_containers(graph):
    registry = build(graph)
    task = parse_record(TaskContext, graph['TaskContext'], policy=POLICY)
    assert registry.register_record(TaskContext, task, session_id=SESSION) == task
    ref = TaskRef(task.task_id, task.revision)
    assert registry.get_record(TaskContext, ref, session_id=SESSION) == task
    message = parse_message(graph['messages']['ControlSnapshot'], policy=POLICY)
    assert registry.register_message(message) == message
    assert registry.get_message(message.meta.message_id, session_id=SESSION) == message


@pytest.mark.parametrize('kind,key,field,value', [
    ('SessionConfig', 'SessionConfig', 'max_xy_speed_mps', .04),
    ('TaskContext', 'TaskContext', 'instruction', 'Changed instruction'),
    ('SceneObservation', 'SceneObservation', 'calibration_id', 'other-calibration'),
    ('SceneGeometry', 'SceneGeometry', 'revision', 2),
    ('SemanticContext', 'SemanticContext', 'task_revision', 2),
    ('ControlSnapshot', 'ControlSnapshot', 'dt_s', .1),
    ('ObjectWeights', 'ObjectWeights', 'assistance_strength', .2),
])
def test_existing_record_identity_cannot_change_content(graph, kind, key, field, value):
    registry = build(graph)
    changed = deepcopy(graph[key])
    changed[field] = value
    assert_error(
        lambda: registry.register_record(kind, changed, session_id=SESSION),
        'immutable_conflict',
    )


@pytest.mark.parametrize('field,value', [
    ('description', 'Different interpretation'),
    ('object_id', 'object-cup-offline-v1'),
    ('keypoint_bindings', ['kp-cup-approach-offline-v1']),
    ('axis_bindings', ['new-axis']),
    ('parameters', [{'name': 'distance_tolerance', 'value': .1, 'unit': 'm'}]),
    ('output_unit', 'dimensionless'),
])
def test_program_identity_covers_more_than_matching_source_hash(graph, field, value):
    registry = build(graph)
    changed = deepcopy(graph['programs'][0])
    changed[field] = value
    assert_error(
        lambda: registry.register_record('ConstraintProgram', changed, session_id=SESSION),
        'immutable_conflict',
    )


@pytest.mark.parametrize('key,kind,field,value', [
    ('bindings', 'ProgramExecutionBinding', 'normalization_scale', .02),
    ('bindings', 'ProgramExecutionBinding', 'weight', .2),
    ('receipts', 'ProgramValidationReceipt', 'validator_policy_version', 'other-policy'),
])
def test_binding_and_receipt_identities_are_immutable(graph, key, kind, field, value):
    registry = build(graph)
    changed = deepcopy(graph[key][0])
    changed[field] = value
    assert_error(
        lambda: registry.register_record(kind, changed, session_id=SESSION),
        'immutable_conflict',
    )


def test_source_hash_covers_exact_utf8_and_line_endings_without_execution(graph):
    registry = SessionRegistry(SESSION, policy=POLICY)
    program = deepcopy(graph['programs'][0])
    program['source_code'] = 'raise AssertionError("never execute")\r\n# café\n'
    program['source_sha256'] = sha256(program['source_code'].encode('utf-8')).hexdigest()
    result = registry.register_record('ConstraintProgram', program, session_id=SESSION)
    assert result.source_code == program['source_code']
    changed = deepcopy(program)
    changed['program_id'] = 'program-wrong-source-hash'
    changed['source_code'] = changed['source_code'].replace('\r\n', '\n')
    assert_error(
        lambda: registry.register_record('ConstraintProgram', changed, session_id=SESSION),
        'source_hash_mismatch',
    )
    assert_error(
        lambda: registry.get_record('ConstraintProgram', changed['program_id'], session_id=SESSION),
        'missing_reference',
    )


def test_input_and_export_mutation_cannot_change_registered_or_resolved_values(graph):
    registry = build(graph)
    before = registry.resolve_snapshot('snapshot-test')
    graph['SceneGeometry']['keypoints'][0]['position_xyz'][0] = 100
    graph['programs'][0]['parameters'][0]['value'] = 100
    graph['messages']['JoystickSample']['payload']['u_xy'][0] = 0
    exported = to_dict(before.bundle.geometry)
    exported['keypoints'][0]['position_xyz'][0] = 200
    after = registry.resolve_snapshot('snapshot-test')
    assert after == before
    assert after.bundle.programs[0].params == (0,)
    assert after.joystick.payload.u_xy == (1, 0)


def test_message_conflict_and_payload_conflict_leave_no_partial_registration(graph):
    registry = build(graph)
    original = graph['messages']['SessionConfig']
    changed_message = deepcopy(original)
    changed_message['payload']['config_id'] = 'must-not-be-registered'
    assert_error(lambda: registry.register_message(changed_message), 'immutable_conflict')
    assert_error(
        lambda: registry.get_record('SessionConfig', 'must-not-be-registered', session_id=SESSION),
        'missing_reference',
    )
    changed_payload = deepcopy(original)
    changed_payload['meta']['message_id'] = 'must-not-be-published'
    changed_payload['payload']['max_xy_speed_mps'] = .01
    assert_error(lambda: registry.register_message(changed_payload), 'immutable_conflict')
    assert_error(
        lambda: registry.get_message('must-not-be-published', session_id=SESSION),
        'missing_reference',
    )
    assert registry.resolve_snapshot('snapshot-test').bundle.configuration.max_xy_speed_mps == .05


def test_other_session_is_rejected_for_registration_and_lookup(graph):
    registry = build(graph)
    message = deepcopy(graph['messages']['JoystickSample'])
    message['meta']['session_id'] = 'another-session'
    assert_error(lambda: registry.register_message(message), 'session_mismatch')
    assert_error(
        lambda: registry.register_record('TaskContext', graph['TaskContext'], session_id='another-session'),
        'session_mismatch',
    )
    assert_error(
        lambda: registry.get_message('joystick-current', session_id='another-session'),
        'session_mismatch',
    )
    assert_error(
        lambda: registry.get_record('SessionConfig', graph['SessionConfig']['config_id'], session_id='another-session'),
        'session_mismatch',
    )


def test_live_and_parameter_messages_need_no_invented_payload_identifier(graph):
    registry = build(graph)
    parameter = envelope('Parameter', graph['programs'][0]['parameters'][0], 'parameter-message')
    result = registry.register_message(parameter)
    assert result.payload.name == 'distance_tolerance'
    assert registry.get_message('parameter-message', session_id=SESSION) == result


@pytest.mark.parametrize('field,value', [
    ('program_id', 'missing-program'),
    ('source_sha256', '0' * 64),
])
def test_program_reference_requires_id_and_hash(graph, field, value):
    registry = build(graph)
    reference = deepcopy(graph['ControlSnapshot']['program_refs'][0])
    reference[field] = value
    assert_error(lambda: registry.resolve_program(
        reference, geometry_ref=graph['ControlSnapshot']['geometry_ref'],
        config_id=graph['SessionConfig']['config_id'],
    ))


@pytest.mark.parametrize('field,value', [
    ('object_id', 'missing-object'),
    ('keypoint_bindings', ['missing-keypoint']),
    ('axis_bindings', ['missing-axis']),
])
def test_program_never_substitutes_missing_object_or_feature_by_position(graph, field, value):
    graph['programs'][0][field] = value
    registry = build(graph)
    assert_error(lambda: registry.resolve_program(
        graph['ControlSnapshot']['program_refs'][0],
        geometry_ref=graph['ControlSnapshot']['geometry_ref'],
        config_id=graph['SessionConfig']['config_id'],
    ), 'missing_reference')


def add_ordered_features(graph):
    geometry = graph['SceneGeometry']
    for index, point in enumerate(geometry['keypoints']):
        geometry['axes'].append({
            'axis_id': f'axis-{index}', 'object_id': point['object_id'],
            'origin_keypoint_id': point['keypoint_id'],
            'unit_direction_xyz': [1, 0, 0] if index == 0 else [0, 1, 0],
            'directed': True,
        })
    program = graph['programs'][0]
    # Cross-object geometric relations are legal; candidate identity only
    # selects which object's assistance cost receives this relation.
    program['keypoint_bindings'] = [point['keypoint_id'] for point in reversed(geometry['keypoints'])]
    program['axis_bindings'] = ['axis-1', 'axis-0']
    program['parameters'] = [
        {'name': 'second', 'value': .2, 'unit': 'm'},
        {'name': 'first', 'value': .1, 'unit': 'm'},
    ]


def test_abi_order_comes_from_bindings_not_geometry_storage_order(graph):
    add_ordered_features(graph)
    first = build(graph).resolve_snapshot('snapshot-test')
    for field in ('objects', 'keypoints', 'axes'):
        graph['SceneGeometry'][field].reverse()
    graph['programs'].reverse()
    graph['bindings'].reverse()
    graph['receipts'].reverse()
    graph['ObjectWeights']['weights'].reverse()
    second = build(graph).resolve_weights('weights-test')
    first_programs = {item.program.program_id: item for item in first.bundle.programs}
    for item in second.snapshot.bundle.programs:
        prior = first_programs[item.program.program_id]
        assert (item.keypoints, item.axis_dirs, item.params) == (prior.keypoints, prior.axis_dirs, prior.params)
    bottle = first_programs['program-bottle-distance-offline-v1']
    assert bottle.keypoints == ((.39, 0, .3), (.41, 0, .3))
    assert bottle.axis_dirs == ((0, 1, 0), (1, 0, 0))
    assert bottle.params == (.2, .1)
    assert [item.object_id for item in second.candidate_weights] == [
        item['object_id'] for item in graph['SemanticContext']['object_relevance']
    ]


@pytest.mark.parametrize('key,field,value', [
    ('SessionConfig', 'validation_policy_id', 'other-policy'),
    ('SceneGeometry', 'frame_id', 'other-frame'),
    ('SceneGeometry', 'scene_id', 'other-scene'),
    ('SceneGeometry', 'observation_id', 'missing-observation'),
    ('SemanticContext', 'task_revision', 2),
    ('SemanticContext', 'task_id', 'other-task'),
    ('SemanticContext', 'scene_id', 'other-scene'),
    ('SemanticContext', 'feature_set_id', 'other-features'),
    ('SemanticContext', 'observation_id', 'missing-observation'),
])
def test_bundle_rejects_incompatible_dependencies(graph, key, field, value):
    graph[key][field] = value
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)))


def test_observation_capture_frame_must_match_configuration(graph):
    graph['SceneObservation']['T_control_camera']['parent_frame'] = 'other-control-frame'
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)), 'incompatible_reference')


@pytest.mark.parametrize('field,value', [
    ('config_id', 'missing-config'),
    ('semantic_id', 'missing-semantic'),
    ('geometry_ref', {'geometry_id': 'missing-geometry', 'revision': 1}),
    ('task_ref', {'task_id': 'missing-task', 'revision': 1}),
])
def test_bundle_has_no_fallback_for_missing_references(graph, field, value):
    registry = build(graph)
    arguments = bundle_args(graph)
    arguments[field] = value
    assert_error(lambda: registry.resolve_bundle(**arguments), 'missing_reference')


def test_geometry_reference_checks_revision_and_task_reference_checks_full_version(graph):
    registry = build(graph)
    arguments = bundle_args(graph)
    arguments['geometry_ref']['revision'] = 2
    assert_error(lambda: registry.resolve_bundle(**arguments), 'incompatible_reference')
    changed_task = deepcopy(graph['TaskContext'])
    changed_task.update(revision=2, instruction='A later task instruction')
    registry.register_record('TaskContext', changed_task, session_id=SESSION)
    arguments = bundle_args(graph)
    arguments['task_ref']['revision'] = 2
    assert_error(lambda: registry.resolve_bundle(**arguments), 'incompatible_reference')
    assert registry.resolve_snapshot('snapshot-test').bundle.task.revision == 1
    assert registry.get_record('TaskContext', TaskRef(changed_task['task_id'], 2), session_id=SESSION).revision == 2


@pytest.mark.parametrize('field,value', [
    ('status', 'rejected'),
    ('program_id', 'other-program'),
    ('source_sha256', '0' * 64),
    ('scene_id', 'other-scene'),
    ('feature_set_id', 'other-features'),
    ('validator_policy_version', 'other-policy'),
    ('validation_geometry_id', 'missing-validation-geometry'),
    ('validation_geometry_revision', 2),
])
def test_receipt_compatibility_includes_exact_validation_provenance(graph, field, value):
    graph['receipts'][0][field] = value
    if field == 'status':
        graph['receipts'][0]['diagnostics'] = ['Synthetic rejection for test']
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)))


@pytest.mark.parametrize('field,value', [
    ('config_id', 'other-config'),
    ('program_id', 'other-program'),
    ('validation_receipt_id', 'missing-receipt'),
])
def test_binding_dependencies_must_match_current_bundle(graph, field, value):
    graph['bindings'][0][field] = value
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)))


def test_candidates_programs_and_execution_bindings_have_complete_coverage(graph):
    registry = build(graph)
    arguments = bundle_args(graph)
    arguments['program_refs'] = arguments['program_refs'][:1]
    assert_error(lambda: registry.resolve_bundle(**arguments), 'program_coverage')
    arguments = bundle_args(graph)
    arguments['execution_binding_refs'] = arguments['execution_binding_refs'][:1]
    assert_error(lambda: registry.resolve_bundle(**arguments), 'binding_coverage')
    graph['programs'][1]['object_id'] = graph['programs'][0]['object_id']
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)), 'candidate_coverage')


def test_non_candidate_program_is_rejected_even_when_object_exists(graph):
    graph['SemanticContext']['object_relevance'].pop()
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)), 'candidate_coverage')


def test_coordinate_revision_reuses_receipt_and_preserves_old_snapshot(graph):
    registry = build(graph)
    old = registry.resolve_snapshot('snapshot-test')
    newer = deepcopy(graph['SceneGeometry'])
    newer.update(geometry_id='geometry-revision-2', revision=2)
    newer['keypoints'][0]['position_xyz'][0] += .01
    registry.register_record('SceneGeometry', newer, session_id=SESSION)
    arguments = bundle_args(graph)
    arguments['geometry_ref'] = {'geometry_id': newer['geometry_id'], 'revision': 2}
    new_bundle = registry.resolve_bundle(**arguments)
    assert new_bundle.geometry.revision == 2
    assert all(receipt.validation_geometry_revision == 1 for receipt in new_bundle.receipts)
    assert registry.resolve_snapshot('snapshot-test') == old
    assert old.bundle.geometry.keypoints[0].position_xyz == (.41, 0, .3)


@pytest.mark.parametrize('change', ['membership', 'ownership', 'axis_directedness', 'axis_origin'])
def test_feature_definition_changes_cannot_reuse_feature_set(graph, change):
    add_ordered_features(graph)
    registry = build(graph)
    changed = deepcopy(graph['SceneGeometry'])
    changed.update(geometry_id='geometry-bad-definition', revision=2)
    if change == 'membership':
        changed['keypoints'].append({
            'keypoint_id': 'new-feature', 'object_id': changed['objects'][0]['object_id'],
            'position_xyz': [.1, .2, .3],
        })
    elif change == 'ownership':
        changed['keypoints'][0]['object_id'] = changed['objects'][1]['object_id']
        changed['axes'][0]['object_id'] = changed['objects'][1]['object_id']
    elif change == 'axis_directedness':
        changed['axes'][0]['directed'] = False
    else:
        changed['keypoints'].append({
            'keypoint_id': 'second-origin', 'object_id': changed['objects'][0]['object_id'],
            'position_xyz': [.1, .2, .3],
        })
        changed['axes'][0]['origin_keypoint_id'] = 'second-origin'
    assert_error(
        lambda: registry.register_record('SceneGeometry', changed, session_id=SESSION),
        'feature_definition_mismatch',
    )
    assert_error(
        lambda: registry.get_record('SceneGeometry', changed['geometry_id'], session_id=SESSION),
        'missing_reference',
    )


@pytest.mark.parametrize('field,value', [
    ('joystick_ref', 'missing-joystick'),
    ('robot_state_ref', 'missing-state'),
    ('joystick_ref', 'robot-state-current'),
    ('robot_state_ref', 'joystick-current'),
    ('joystick_history_refs', ['robot-state-current']),
    ('joystick_history_refs', ['missing-history']),
])
def test_snapshot_input_references_are_exact_messages_of_expected_type(graph, field, value):
    graph['ControlSnapshot'][field] = value
    assert_error(lambda: build(graph).resolve_snapshot('snapshot-test'))


@pytest.mark.parametrize('field', ['frame_id', 'tcp_frame'])
def test_snapshot_robot_frames_match_configuration(graph, field):
    graph['messages']['RobotState']['payload'][field] = 'wrong-frame'
    assert_error(lambda: build(graph).resolve_snapshot('snapshot-test'), 'incompatible_reference')


@pytest.mark.parametrize('weights', [
    [{'object_id': 'object-bottle-offline-v1', 'weight': 1}],
    [{'object_id': 'object-bottle-offline-v1', 'weight': .5}, {'object_id': 'extra-object', 'weight': .5}],
])
def test_weight_candidates_match_exact_semantic_set_without_repair(graph, weights):
    graph['ObjectWeights']['weights'] = weights
    assert_error(lambda: build(graph).resolve_weights('weights-test'), 'candidate_coverage')


def test_weights_require_the_exact_snapshot(graph):
    graph['ObjectWeights']['snapshot_id'] = 'missing-snapshot'
    assert_error(lambda: build(graph).resolve_weights('weights-test'), 'missing_reference')


def test_direct_dataclass_construction_does_not_bypass_validation(graph):
    registry = build(graph)
    task = parse_record('TaskContext', graph['TaskContext'], policy=POLICY)
    with pytest.raises(ValidationError):
        registry.register_record('TaskContext', replace(task, revision=True), session_id=SESSION)
    with pytest.raises(ValidationError):
        registry.resolve_program(
            ProgramRef(graph['programs'][0]['program_id'], 'not-a-hash'),
            geometry_ref=graph['ControlSnapshot']['geometry_ref'],
            config_id=graph['SessionConfig']['config_id'],
        )
    with pytest.raises(ValidationError):
        registry.resolve_program(
            ProgramRef(**graph['ControlSnapshot']['program_refs'][0]),
            geometry_ref=GeometryRef(graph['SceneGeometry']['geometry_id'], True),
            config_id=graph['SessionConfig']['config_id'],
        )
    message = parse_message(graph['messages']['JoystickSample'], policy=POLICY)
    with pytest.raises(ValidationError):
        registry.register_message(replace(message, payload=replace(message.payload, u_xy=(2, 0))))


def test_resolution_does_not_assert_receipt_trust_or_runtime_freshness(graph):
    # These checks belong to the later trusted validator and robot lifecycle.
    graph['messages']['JoystickSample']['payload']['sampled_at']['sec'] = -100
    graph['messages']['RobotState']['payload']['sampled_at']['sec'] = -100
    graph['ControlSnapshot']['previous_applied_command_id'] = 'external-local-adapter-record'
    assert build(graph).resolve_snapshot('snapshot-test').joystick.payload.sampled_at.sec == -100


def test_parsed_inputs_and_lookup_results_are_detached_even_from_frozen_bypass(graph):
    registry = build(graph)
    original = parse_record('ConstraintProgram', graph['programs'][0], policy=POLICY)
    registered = registry.register_record('ConstraintProgram', original, session_id=SESSION)
    resolved = registry.resolve_snapshot('snapshot-test')
    object.__setattr__(original.parameters[0], 'value', 100)
    object.__setattr__(registered.parameters[0], 'value', 200)
    message = registry.get_message('robot-state-current', session_id=SESSION)
    object.__setattr__(message.payload.tcp_pose, 'position_xyz', (100, 200, 300))
    assert registry.resolve_snapshot('snapshot-test') == resolved
    assert resolved.bundle.programs[0].params == (0,)


def test_new_program_and_binding_versions_leave_old_resolved_values_intact(graph):
    registry = build(graph)
    old = registry.resolve_snapshot('snapshot-test')
    program = deepcopy(graph['programs'][0])
    program['program_id'] = 'bottle-program-v2'
    program['parameters'][0]['value'] = .01
    receipt = deepcopy(graph['receipts'][0])
    receipt.update(receipt_id='bottle-receipt-v2', program_id=program['program_id'])
    config = deepcopy(graph['SessionConfig'])
    config['config_id'] = 'config-v2'
    semantic = deepcopy(graph['SemanticContext'])
    semantic['semantic_id'] = 'semantic-v2'
    semantic['program_refs'][0]['program_id'] = program['program_id']
    bindings = deepcopy(graph['bindings'])
    for index, binding in enumerate(bindings):
        binding.update(binding_id=f'binding-v2-{index}', config_id=config['config_id'])
    bindings[0].update(program_id=program['program_id'], validation_receipt_id=receipt['receipt_id'], weight=.25)
    for kind, data in [('ConstraintProgram', program), ('ProgramValidationReceipt', receipt),
                       ('SessionConfig', config), ('SemanticContext', semantic)]:
        registry.register_record(kind, data, session_id=SESSION)
    for binding in bindings:
        registry.register_record('ProgramExecutionBinding', binding, session_id=SESSION)
    arguments = bundle_args(graph)
    arguments.update(config_id=config['config_id'], semantic_id=semantic['semantic_id'],
                     program_refs=semantic['program_refs'],
                     execution_binding_refs=[binding['binding_id'] for binding in bindings])
    new = registry.resolve_bundle(**arguments)
    assert new.programs[0].params == (.01,)
    assert new.programs[0].program.source_sha256 == old.bundle.programs[0].program.source_sha256
    assert new.execution_bindings[0].weight == .25
    assert old.bundle.programs[0].params == (0,)
    assert registry.resolve_snapshot('snapshot-test') == old


@pytest.mark.parametrize('field', ['program_refs', 'execution_binding_refs'])
def test_duplicate_program_or_binding_requests_do_not_double_count_costs(graph, field):
    registry = build(graph)
    arguments = bundle_args(graph)
    arguments[field].append(deepcopy(arguments[field][0]))
    assert_error(lambda: registry.resolve_bundle(**arguments),
                 'program_coverage' if field == 'program_refs' else 'binding_coverage')


def test_missing_candidate_object_is_not_dropped_or_replaced(graph):
    graph['SemanticContext']['object_relevance'][0]['object_id'] = 'absent-candidate'
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)), 'missing_reference')


def test_zero_relevance_candidates_still_require_programs(graph):
    graph['SemanticContext']['object_relevance'][1]['task_relevance'] = 0
    graph['SemanticContext']['program_refs'].pop()
    graph['ControlSnapshot']['program_refs'].pop()
    graph['ControlSnapshot']['execution_binding_refs'].pop()
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)), 'candidate_coverage')


def test_empty_feature_and_parameter_bindings_resolve_without_fabricated_values(graph):
    graph['programs'][0].update(keypoint_bindings=[], axis_bindings=[], parameters=[])
    result = build(graph).resolve_snapshot('snapshot-test').bundle.programs[0]
    assert (result.keypoints, result.axis_dirs, result.params) == ((), (), ())


def test_earlier_session_messages_are_not_available_in_new_session(graph):
    old = build(graph)
    new = SessionRegistry('new-session', policy=POLICY)
    assert_error(lambda: new.get_message('joystick-current', session_id='new-session'), 'missing_reference')
    assert_error(lambda: new.get_record('SceneGeometry', graph['SceneGeometry']['geometry_id'],
                                       session_id='new-session'), 'missing_reference')
    assert_error(lambda: new.register_message(old.get_message('joystick-current', session_id=SESSION)),
                 'session_mismatch')


def test_capture_skew_checks_original_timestamps_against_config(graph):
    observation = graph['SceneObservation']
    observation['depth_stamp'] = deepcopy(observation['rgb_stamp'])
    observation['depth_stamp']['sec'] += 1
    assert_error(lambda: build(graph).resolve_bundle(**bundle_args(graph)), 'incompatible_reference')


@pytest.mark.parametrize('field', ['mapping_id', 'sampled_at'])
def test_history_cannot_use_another_input_mapping_or_clock(graph, field):
    sample = graph['messages']['JoystickHistory']['payload']
    if field == 'mapping_id':
        sample[field] = 'different-mapping'
    else:
        sample[field]['clock_id'] = 'different-clock'
    assert_error(lambda: build(graph).resolve_snapshot('snapshot-test'), 'incompatible_reference')


def test_new_geometry_id_cannot_reuse_revision_for_changed_coordinates(graph):
    registry = build(graph)
    geometry = deepcopy(graph['SceneGeometry'])
    geometry['geometry_id'] = 'geometry-changed-without-revision'
    geometry['keypoints'][0]['position_xyz'][0] += .01
    message = envelope('SceneGeometry', geometry, 'failed-geometry-envelope')
    assert_error(lambda: registry.register_message(message), 'geometry_revision_conflict')
    assert_error(lambda: registry.get_message(message['meta']['message_id'], session_id=SESSION), 'missing_reference')
    assert_error(lambda: registry.get_record('SceneGeometry', geometry['geometry_id'], session_id=SESSION),
                 'missing_reference')
    geometry['revision'] += 1
    registry.register_message(message)
    arguments = bundle_args(graph)
    arguments['geometry_ref'] = {'geometry_id': geometry['geometry_id'], 'revision': geometry['revision']}
    assert registry.resolve_bundle(**arguments).geometry.keypoints[0].position_xyz[0] == .42


def test_identical_geometry_revision_values_allow_storage_permutations(graph):
    add_ordered_features(graph)
    registry = build(graph)
    geometry = deepcopy(graph['SceneGeometry'])
    geometry['geometry_id'] = 'equivalent-coordinate-version'
    for field in ('objects', 'keypoints', 'axes'):
        geometry[field].reverse()
    registry.register_record('SceneGeometry', geometry, session_id=SESSION)
    arguments = bundle_args(graph)
    arguments['geometry_ref']['geometry_id'] = geometry['geometry_id']
    actual = registry.resolve_bundle(**arguments).programs[0]
    assert actual.keypoints == ((.39, 0, .3), (.41, 0, .3))
    assert actual.axis_dirs == ((0, 1, 0), (1, 0, 0))


@pytest.mark.parametrize('field', ['normalization_scale', 'weight'])
def test_new_binding_id_cannot_change_settings_under_same_configuration(graph, field):
    registry = build(graph)
    binding = deepcopy(graph['bindings'][0])
    binding['binding_id'] = 'binding-changed-without-config'
    binding[field] *= 2
    message = envelope('ProgramExecutionBinding', binding, 'failed-binding-envelope')
    assert_error(lambda: registry.register_message(message), 'binding_configuration_conflict')
    assert_error(lambda: registry.get_message(message['meta']['message_id'], session_id=SESSION), 'missing_reference')
    assert_error(lambda: registry.get_record('ProgramExecutionBinding', binding['binding_id'], session_id=SESSION),
                 'missing_reference')
    binding['config_id'] = 'new-configuration'
    assert registry.register_message(message).payload.config_id == 'new-configuration'


def test_same_execution_settings_allow_a_new_receipt_reference(graph):
    registry = build(graph)
    binding = deepcopy(graph['bindings'][0])
    binding.update(binding_id='same-settings-new-receipt', validation_receipt_id='refreshed-receipt')
    receipt = deepcopy(graph['receipts'][0])
    receipt['receipt_id'] = binding['validation_receipt_id']
    registry.register_record('ProgramValidationReceipt', receipt, session_id=SESSION)
    registry.register_record('ProgramExecutionBinding', binding, session_id=SESSION)
    arguments = bundle_args(graph)
    arguments['execution_binding_refs'][0] = binding['binding_id']
    assert registry.resolve_bundle(**arguments).receipts[0].receipt_id == receipt['receipt_id']


@pytest.mark.parametrize('sign', [-1, 1])
def test_unbounded_timestamp_seconds_remain_idempotent_without_decimal_encoding(graph, sign):
    registry = SessionRegistry(SESSION, policy=POLICY)
    message = deepcopy(graph['messages']['JoystickSample'])
    seconds = sign * 10 ** 5000
    message['meta']['produced_at']['sec'] = seconds
    message['payload']['sampled_at']['sec'] = seconds
    first = registry.register_message(message)
    assert registry.register_message(message) == first
    assert registry.get_message(first.meta.message_id, session_id=SESSION) == first


@pytest.mark.parametrize('before,after', [(0, 0.0), (0.0, -0.0)])
def test_identity_preserves_numeric_type_and_signed_zero(graph, before, after):
    registry = SessionRegistry(SESSION, policy=POLICY)
    message = deepcopy(graph['messages']['JoystickSample'])
    message['payload']['u_xy'][1] = before
    registry.register_message(message)
    message['payload']['u_xy'][1] = after
    assert_error(lambda: registry.register_message(message), 'immutable_conflict')
