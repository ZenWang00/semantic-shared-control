from copy import deepcopy
from dataclasses import replace

import pytest

from ssc_core import (
    CandidateSet, ControlSnapshot, NUMERIC_CANDIDATES_V2 as POLICY,
    RegistryError, estimate_intent, get_intent_config, to_dict,
)


def bundle_args(catalog):
    data={m['message_type']:m['payload'] for m in catalog['messages']}
    return dict(config_id=data['SessionConfig']['config_id'],
                task_ref=dict(task_id=data['TaskContext']['task_id'], revision=data['TaskContext']['revision']),
                geometry_ref=dict(geometry_id=data['SceneGeometry']['geometry_id'],revision=data['SceneGeometry']['revision']),
                semantic_id=data['SemanticContext']['semantic_id'],candidate_set_id=data['CandidateSet']['candidate_set_id'])


def test_bundle_resolves_data_only(offline_catalog,make_registry):
    bundle=make_registry(offline_catalog).resolve_bundle(**bundle_args(offline_catalog))
    assert len(bundle.candidates)==2
    assert not hasattr(bundle,'programs')


@pytest.mark.parametrize(('field','value'),[
    ('frame_id','camera'),('task_revision',2),('scene_id','other'),
    ('observation_id','unknown'),('feature_set_id','other'),
])
def test_reject_candidate_context_mismatch(offline_catalog,field,value,make_registry):
    raw=next(m['payload'] for m in offline_catalog['messages'] if m['message_type']=='CandidateSet')
    raw[field]=value
    with pytest.raises(RegistryError):
        make_registry(offline_catalog).resolve_bundle(**bundle_args(offline_catalog))


def test_immutable_candidates_and_foreign_owner(offline_catalog,make_registry):
    registry=make_registry(offline_catalog)
    raw=deepcopy(next(m['payload'] for m in offline_catalog['messages'] if m['message_type']=='CandidateSet'))
    raw['candidates'][0]['position_xyz'][2]=.4
    with pytest.raises(RegistryError,match='immutable_conflict'):
        registry.register_record(CandidateSet,raw,session_id=registry.session_id)
    raw['candidate_set_id']='new'
    raw['candidates'][0]['object_id']='unknown'
    registry.register_record(CandidateSet,raw,session_id=registry.session_id)
    args=bundle_args(offline_catalog);args['candidate_set_id']='new'
    with pytest.raises(RegistryError,match='candidate_coverage'):
        registry.resolve_bundle(**args)


def test_previous_selection_pins_earlier_same_context(make_resolved):
    prior=make_resolved()
    selection=estimate_intent(prior,get_intent_config('intent-candidates-v2'),selection_id='prior',policy=POLICY).selection
    current=make_resolved(seq=2,previous_selection=selection,previous_snapshot=prior.snapshot)
    assert current.previous_selection==selection
    for changes in ({'control_epoch':2},{'snapshot_seq':2},{'candidate_set_id':'other'}):
        with pytest.raises(RegistryError):
            make_resolved(seq=2,previous_selection=selection,previous_snapshot=replace(prior.snapshot,**changes))


def test_candidate_array_order_does_not_change_immutable_set(offline_catalog,make_registry):
    registry=make_registry(offline_catalog)
    raw=deepcopy(next(m['payload'] for m in offline_catalog['messages'] if m['message_type']=='CandidateSet'))
    raw['candidates'].reverse()
    registry.register_record(CandidateSet,raw,session_id=registry.session_id)
    assert len(registry.resolve_bundle(**bundle_args(offline_catalog)).candidates)==2
