"""Contract fixtures; catalogs are inputs, never trusted runtime receipts."""

from copy import deepcopy
import json
from pathlib import Path

import pytest


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / 'fixtures' / 'shared_control'
SUPPORTED_MESSAGE_TYPES = {
    'SessionConfig', 'TaskContext', 'SceneObservation', 'SceneGeometry',
    'SemanticContext', 'ConstraintProgram', 'Parameter',
    'ProgramExecutionBinding', 'ProgramValidationReceipt',
}


@pytest.fixture
def illustrative_catalog():
    return json.loads((FIXTURE_ROOT / 'bottle_cup_illustrative.json').read_text())


@pytest.fixture
def offline_catalog():
    return json.loads((FIXTURE_ROOT / 'bottle_cup_offline_v1.json').read_text())


@pytest.fixture
def records(illustrative_catalog):
    """Independent mutable payload per test, from the published example."""
    result = {
        m['message_type']: deepcopy(m['payload'])
        for m in illustrative_catalog['messages']
        if m['message_type'] in SUPPORTED_MESSAGE_TYPES
    }
    result['ConstraintProgram'] = deepcopy(illustrative_catalog['constraint_programs'][0])
    result['ProgramExecutionBinding'] = deepcopy(illustrative_catalog['execution_bindings'][0])
    result['ProgramValidationReceipt'] = deepcopy(illustrative_catalog['validation_receipts'][0])
    result['Parameter'] = deepcopy(result['ConstraintProgram']['parameters'][0])
    observation = result['SceneObservation']
    result.update({
        'TimePoint': deepcopy(observation['rgb_stamp']),
        'Pose': deepcopy(observation['T_control_camera']['pose']),
        'StampedTransform': deepcopy(observation['T_control_camera']),
        'AssetRef': deepcopy(observation['rgb_ref']),
        'ProgramRef': deepcopy(result['SemanticContext']['program_refs'][0]),
        'GeometryRef': {'geometry_id': result['SceneGeometry']['geometry_id'], 'revision': 1},
        'MessageMeta': deepcopy(illustrative_catalog['messages'][0]['meta']),
        'Object': deepcopy(result['SceneGeometry']['objects'][0]),
        'Keypoint': deepcopy(result['SceneGeometry']['keypoints'][0]),
        'Axis': deepcopy(result['SceneGeometry']['axes'][0]),
        'ObjectRelevance': deepcopy(result['SemanticContext']['object_relevance'][0]),
    })
    return result
