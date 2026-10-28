from copy import deepcopy
from dataclasses import replace
from math import sqrt

import pytest

from ssc_core import (
    NUMERIC_CANDIDATES_V2 as POLICY, estimate_intent, get_intent_config,
)

CONFIG=get_intent_config('intent-candidates-v2')


def select(resolved, selection_id='selection', config=CONFIG):
    outcome=estimate_intent(resolved,config,selection_id=selection_id,policy=POLICY)
    assert outcome.disposition in ('active','disabled'), outcome
    return outcome.selection


def test_direction_selects_one_real_candidate_and_replays(resolved,make_resolved):
    selected=select(resolved)
    assert selected.selected_candidate_id=='candidate-bottle-v2'
    scores={item.candidate_id:item.score for item in selected.scores}
    assert scores=={'candidate-bottle-v2':3.0,'candidate-cup-v2':1.0}
    assert selected.assistance_strength==.5
    assert select(resolved)==selected
    reverse=select(make_resolved(u_xy=(-1.,0.)))
    assert reverse.selected_candidate_id=='candidate-cup-v2'


def test_input_order_cannot_change_selection(offline_catalog,make_resolved):
    original=select(make_resolved(offline_catalog))
    for message in offline_catalog['messages']:
        if message['message_type']=='CandidateSet':message['payload']['candidates'].reverse()
        if message['message_type']=='SemanticContext':message['payload']['object_relevance'].reverse()
    assert select(make_resolved(offline_catalog))==original


def test_same_object_multiple_candidates_tie_distance_then_id(offline_catalog,make_resolved):
    candidate=next(m['payload'] for m in offline_catalog['messages'] if m['message_type']=='CandidateSet')
    semantic=next(m['payload'] for m in offline_catalog['messages'] if m['message_type']=='SemanticContext')
    semantic['object_relevance']=semantic['object_relevance'][:1]
    owner=semantic['object_relevance'][0]['object_id']
    candidate['candidates']=[dict(candidate_id=key,object_id=owner,position_xyz=position)
                            for key,position in [('z-near',[.41,0,.3]),('a-far',[.41,0,.4]),('a-near',[.41,0,.3])]]
    assert select(make_resolved(offline_catalog)).selected_candidate_id=='a-near'


@pytest.mark.parametrize(('dx','expected'),[(-.04,'candidate-bottle-v2'),(-.05,'candidate-bottle-v2'),(-.051,'candidate-cup-v2')])
def test_switch_requires_strictly_more_than_point_one(make_resolved,dx,expected):
    before=make_resolved()
    previous=select(before,'previous')
    after=make_resolved(seq=2,u_xy=(dx,sqrt(1-dx*dx)),previous_selection=previous,previous_snapshot=before.snapshot)
    assert select(after).selected_candidate_id==expected


@pytest.mark.parametrize(('u_xy','enabled'),[((0.,0.),True),((1.,0.),False)])
def test_release_clears_selection_and_assistance(make_resolved,u_xy,enabled):
    before=make_resolved()
    previous=select(before,'previous')
    after=make_resolved(seq=2,u_xy=u_xy,enabled=enabled,previous_selection=previous,previous_snapshot=before.snapshot)
    result=select(after)
    assert result.selected_candidate_id is None
    assert result.assistance_strength==0


def test_alpha_zero_keeps_intent_but_disables_assistance(offline_catalog,make_resolved):
    for message in offline_catalog['messages']:
        if message['message_type']=='SessionConfig':message['payload']['config_id']='config-candidates-alpha0-v2'
    selected=select(make_resolved(offline_catalog),config=get_intent_config('intent-candidates-alpha0-v2'))
    assert selected.selected_candidate_id=='candidate-bottle-v2'
    assert selected.assistance_strength==0


def test_direct_constructed_context_and_predecessor_are_revalidated(resolved):
    bad=replace(resolved,bundle=replace(resolved.bundle,candidate_set=replace(resolved.bundle.candidate_set,frame_id='camera')))
    assert estimate_intent(bad,CONFIG,selection_id='bad',policy=POLICY).disposition=='error'
    bad=replace(resolved,previous_selection=select(resolved))
    assert estimate_intent(bad,CONFIG,selection_id='bad',policy=POLICY).code=='selection_lineage'
    bad=replace(CONFIG,switch_threshold=.2)
    assert estimate_intent(resolved,bad,selection_id='bad',policy=POLICY).code=='invalid_config'


def test_zero_projected_direction_ties_by_full_3d_distance(offline_catalog,make_resolved):
    raw=next(m['payload'] for m in offline_catalog['messages'] if m['message_type']=='CandidateSet')
    raw['candidates'][0]['position_xyz']=[.4,0,.4]
    raw['candidates'][1]['position_xyz']=[.4,0,.35]
    assert select(make_resolved(offline_catalog)).selected_candidate_id=='candidate-cup-v2'


def with_history(resolved, *, mapping=None, age_s=0, source=None):
    current=resolved.joystick
    stamp=replace(current.payload.sampled_at,sec=current.payload.sampled_at.sec-age_s)
    earlier=replace(current,meta=replace(current.meta,message_id='earlier',sequence=0,
                    source_id=source or current.meta.source_id,produced_at=stamp),
                    payload=replace(current.payload,sampled_at=stamp,u_xy=(1.,0.),
                                    mapping_id=mapping or current.payload.mapping_id))
    return replace(resolved,snapshot=replace(resolved.snapshot,joystick_history_refs=('earlier',)),
                   joystick_history=(earlier,))


def test_explicit_history_changes_support_and_replays(make_resolved):
    resolved=with_history(make_resolved(u_xy=(0.,1.)))
    selection=select(resolved)
    scores={item.candidate_id:item.score for item in selection.scores}
    assert scores=={'candidate-bottle-v2':2.25,'candidate-cup-v2':1.75}
    assert select(resolved)==selection


@pytest.mark.parametrize('changes',[{'mapping':'different'},{'age_s':2},{'source':'different'}])
def test_incomparable_or_expired_history_rejected(resolved,changes):
    outcome=estimate_intent(with_history(resolved,**changes),CONFIG,selection_id='bad',policy=POLICY)
    assert outcome.disposition=='error'
    assert outcome.code=='invalid_history'
