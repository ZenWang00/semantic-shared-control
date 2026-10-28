from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import math

import pytest

from ssc_core import (
    CandidatePoint, CandidateSelection, CandidateSet, ControlResult,
    NUMERIC_CANDIDATES_V2 as POLICY, ValidationError, loads_message,
    parse_message, parse_record, to_dict,
)


def test_all_catalog_messages_round_trip_and_are_detached(offline_catalog):
    for raw in offline_catalog['messages']:
        record = parse_message(raw, policy=POLICY)
        assert to_dict(record) == raw
        assert loads_message(json.dumps(raw), policy=POLICY) == record
        with pytest.raises(FrozenInstanceError):
            record.message_type = 'changed'


def test_candidate_set_supports_many_points_for_one_object(records):
    data = records['CandidateSet']
    data['candidates'] = [dict(candidate_id=f'p{i}', object_id='bottle', position_xyz=[i*.001,0,.3]) for i in range(16)]
    assert len(parse_record(CandidateSet, data, policy=POLICY).candidates) == 16
    data['candidates'].append(dict(candidate_id='extra', object_id='bottle', position_xyz=[0,0,0]))
    with pytest.raises(ValidationError, match='limit'):
        parse_record(CandidateSet,data,policy=POLICY)


@pytest.mark.parametrize('change', [
    lambda d: d.update(candidates=[]),
    lambda d: d['candidates'].append(deepcopy(d['candidates'][0])),
    lambda d: d['candidates'][0].update(position_xyz=[True,0,0]),
    lambda d: d['candidates'][0].update(position_xyz=['1',0,0]),
    lambda d: d['candidates'][0].update(position_xyz=[math.inf,0,0]),
    lambda d: d['candidates'][0].update(position_xyz=[math.nan,0,0]),
    lambda d: d['candidates'][0].update(position_xyz=[1,0]),
    lambda d: d.update(source_code='def evaluate(): pass'),
])
def test_reject_invalid_candidate_data(records,change):
    change(records['CandidateSet'])
    with pytest.raises(ValidationError):
        parse_record(CandidateSet,records['CandidateSet'],policy=POLICY)


def test_reject_old_wire_version_and_program_types(offline_catalog):
    raw = offline_catalog['messages'][0]
    raw['meta']['schema_version']='1.0'
    with pytest.raises(ValidationError,match='schema_version'):
        parse_message(raw,policy=POLICY)
    with pytest.raises(ValidationError,match='unsupported'):
        parse_record('ConstraintProgram',{},policy=POLICY)
    with pytest.raises(ValidationError,match='duplicate_key'):
        loads_message('{"message_type":"CandidateSet","message_type":"ControlResult"}',policy=POLICY)


def test_selection_requires_real_scored_id():
    data=dict(selection_id='s',snapshot_id='snap',estimator_config_id='intent-candidates-v2',
              candidate_set_id='cs',selected_candidate_id='missing',
              scores=[dict(candidate_id='real',score=1,distance_m=.1)],assistance_strength=.5)
    with pytest.raises(ValidationError,match='selected_candidate_id'):
        parse_record(CandidateSelection,data,policy=POLICY)
    data['selected_candidate_id']=None
    with pytest.raises(ValidationError,match='assistance_strength'):
        parse_record(CandidateSelection,data,policy=POLICY)
