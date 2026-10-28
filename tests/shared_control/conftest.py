"""Schema 2.0 data-only candidates and detached snapshot fixtures."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ssc_core import (
    ControlSnapshot, GeometryRef, JoystickSample, Message, MessageMeta,
    NUMERIC_CANDIDATES_V2, Pose, RobotState, SessionRegistry, TaskRef, TimePoint,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / 'fixtures' / 'shared_control'


def candidate_catalog():
    return json.loads((FIXTURE_ROOT / 'bottle_cup_candidates_v2.json').read_text())


def make_registry(catalog=None):
    catalog = deepcopy(catalog if catalog is not None else candidate_catalog())
    session_id = catalog['messages'][0]['meta']['session_id']
    registry = SessionRegistry(session_id, policy=NUMERIC_CANDIDATES_V2)
    for message in catalog['messages']:
        registry.register_message(message)
    return registry


def make_resolved(catalog=None, *, u_xy=(1.0, 0.0), enabled=True,
                  position=(0.4, 0.0, 0.3), quaternion=(0.0, 0.0, 0.0, 1.0),
                  seq=1, epoch=1, previous_selection=None, previous_snapshot=None):
    catalog = deepcopy(catalog if catalog is not None else candidate_catalog())
    registry = make_registry(catalog)
    payloads = {m['message_type']: m['payload'] for m in catalog['messages']}
    clock = payloads['SceneObservation']['rgb_stamp']['clock_id']
    stamp = TimePoint(clock, 10 + seq, 0)
    def message(kind, payload, suffix):
        return Message(kind, MessageMeta('2.0', registry.session_id, f'{suffix}-{seq}',
                                        'test-source', seq, stamp), payload)
    joy = message('JoystickSample', JoystickSample(stamp, 'base-xy', u_xy, enabled), 'joy')
    state = message('RobotState', RobotState(stamp, 'synthetic_base', 'synthetic_tcp',
                    Pose(position, quaternion), ('q1',), (0.0,), 'measured_feedback'), 'state')
    registry.register_message(joy)
    registry.register_message(state)
    if previous_selection is not None:
        registry.register_record(type(previous_snapshot), previous_snapshot, session_id=registry.session_id)
        registry.register_record(type(previous_selection), previous_selection, session_id=registry.session_id)
    geometry, semantic, candidates, task = (payloads[key] for key in
        ('SceneGeometry', 'SemanticContext', 'CandidateSet', 'TaskContext'))
    snapshot = ControlSnapshot(f'snapshot-{seq}', seq, epoch,
        payloads['SessionConfig']['config_id'], TaskRef(task['task_id'],task['revision']), .2,
        joy.meta.message_id, state.meta.message_id, (),
        GeometryRef(geometry['geometry_id'],geometry['revision']), semantic['semantic_id'],
        candidates['candidate_set_id'], None if previous_selection is None else previous_selection.selection_id, None)
    registry.register_record(ControlSnapshot, snapshot, session_id=registry.session_id)
    return registry.resolve_snapshot(snapshot.snapshot_id)


@pytest.fixture
def offline_catalog():
    return candidate_catalog()


@pytest.fixture
def resolved():
    return make_resolved()


@pytest.fixture
def records(offline_catalog):
    result = {m['message_type']: deepcopy(m['payload']) for m in offline_catalog['messages']}
    return result

@pytest.fixture(name='make_resolved')
def resolved_factory():
    return make_resolved


@pytest.fixture(name='make_registry')
def registry_factory():
    return make_registry
