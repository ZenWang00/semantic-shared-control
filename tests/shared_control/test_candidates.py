from copy import deepcopy
import math

import pytest

from ssc_core import ValidationError, parse_vlm_proposal

CONTEXT=dict(known_object_ids=('bottle','cup'),task_id='task',task_revision=1,scene_id='scene',observation_id='capture')
RESPONSE={'schema_version':'2.0','candidates':[
    {'object_id':'bottle','task_relevance':.9,'point_yx':[250,500]},
    {'object_id':'bottle','task_relevance':.9,'point_yx':[400,500]},
    {'object_id':'cup','task_relevance':.1,'point_yx':[600,700]},
]}


def test_fixed_vlm_response_is_data_only_with_order_independent_host_ids():
    original=parse_vlm_proposal(RESPONSE,**CONTEXT)
    reversed_input=deepcopy(RESPONSE);reversed_input['candidates'].reverse()
    assert parse_vlm_proposal(reversed_input,**CONTEXT)==original
    assert len({item.candidate_id for item in original.candidates})==3
    assert not hasattr(original.candidates[0],'position_xyz')
    assert {p.point_yx for p in original.candidates}=={(250,500),(400,500),(600,700)}


@pytest.mark.parametrize('change',[
    lambda d:d.update(schema_version='1.0'),
    lambda d:d.update(candidates=[]),
    lambda d:d['candidates'][0].update(object_id='foreign'),
    lambda d:d['candidates'][0].update(task_relevance=True),
    lambda d:d['candidates'][0].update(point_yx=[math.nan,0]),
    lambda d:d['candidates'][0].update(point_yx=[1001,0]),
    lambda d:d['candidates'][0].update(point_yx=[0]),
    lambda d:d['candidates'][0].update(candidate_id='model-owned'),
    lambda d:d['candidates'].append(deepcopy(d['candidates'][0])),
    lambda d:d['candidates'][1].update(task_relevance=.7),
    lambda d:d.update(source_code='arbitrary'),
])
def test_reject_invalid_vlm_proposals(change):
    data=deepcopy(RESPONSE);change(data)
    with pytest.raises(ValidationError):parse_vlm_proposal(data,**CONTEXT)


def test_parser_rejects_duplicate_keys_and_no_implicit_grounding():
    with pytest.raises(ValidationError,match='duplicate_key'):
        parse_vlm_proposal('{"schema_version":"2.0","schema_version":"2.0","candidates":[]}',**CONTEXT)
