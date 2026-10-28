"""V2 archive dependency closure, immutable evidence and data-only replay."""
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import pytest
from ssc_core import RegistryError
from ssc_core.archive import ARCHIVE_VERSION, ArchiveBuilder, ArchiveError, RunArchive, inspect_archive
from ssc_core.replay import replay_archive

ROOT=Path(__file__).resolve().parents[2]
F=runpy.run_path(str(ROOT/'docs/reference/archive_fixture.py'))

def encode(value): return json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
def envelope(payload): return encode({'archive_version':ARCHIVE_VERSION,'sha256':sha256(encode(payload)).hexdigest(),'payload':payload})

@pytest.fixture
def archive():
    # Explicit synthetic fixture identities, not tested source/wheel provenance.
    return F['build_fixture'](source_revision='a'*40,wheel_sha256='b'*64)

def builder_from(archive):
    payload=archive.to_dict(); builder=ArchiveBuilder(payload['manifest'])
    for item in payload['records']: builder.add_record(item['record_type'],item['data'])
    for item in payload['messages']: builder.add_message(item)
    for item in payload['resources']:
        builder.add_resource(item['resource_id'],archive.resource(item['resource_id']),media_type=item['media_type'])
    for item in payload['events']: builder.add_event(item['event_id'],item['kind'],item['data'])
    return builder

def test_roundtrip_preserves_explicit_selection_and_numeric_evidence(archive,tmp_path):
    rebuilt=builder_from(archive).seal()
    assert rebuilt.to_dict()==archive.to_dict()
    path=tmp_path/'run.json'; digest=rebuilt.write(path)
    loaded=RunArchive.read(path,expected_sha256=digest)
    assert loaded.to_bytes()==archive.to_bytes()
    assert loaded.manifest['schema_version']=='2.0'
    assert loaded.registry().resolve_snapshot('snapshot-1').previous_selection.selected_candidate_id=='candidate-bottle-v2'
    inspected=inspect_archive(loaded)
    assert len(inspected['proposed_results'])==3 and len(inspected['acceptance_decisions'])==3
    assert inspected['controller_observations']==[]

def test_replay_recomputes_selection_switch_release_and_distances(archive,monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('replay must never launch a solver or executable')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    report=replay_archive(archive)
    assert report['ok'] and report['selection_count']==3 and report['evaluation_count']==2
    assert [s['actual']['selected_candidate_id'] for s in report['selections']]==[
        'candidate-bottle-v2','candidate-cup-v2',None]
    assert all('program_id' not in e for e in report['evaluations'])

def test_detached_reads_no_overwrite_or_symlink_replace(archive,tmp_path):
    payload=archive.to_dict();payload['manifest']['run_id']='changed'
    assert archive.manifest['run_id']!='changed'
    path=tmp_path/'run.json';archive.write(path)
    before=path.read_bytes()
    with pytest.raises(FileExistsError): archive.write(path)
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(FileExistsError): archive.write(link)
    assert path.read_bytes()==before
    assert sorted(p.name for p in tmp_path.iterdir())==['link','run.json']

@pytest.mark.parametrize('kind',['message','record','resource','event'])
def test_changed_identity_rejected(archive,kind):
    builder=builder_from(archive);payload=archive.to_dict()
    with pytest.raises((RegistryError,ArchiveError),match='immutable_conflict'):
        if kind=='message':
            item=next(i for i in payload['messages'] if i['message_type']=='CandidateSet')
            item['payload']['candidates'][0]['position_xyz'][2]+=.01
            builder.add_message(item)
        elif kind=='record':
            item=next(i for i in payload['records'] if i['record_type']=='CandidateSelection')
            item['data']['scores'][0]['score']+=.01
            builder.add_record(item['record_type'],item['data'])
        elif kind=='resource': builder.add_resource('solver-candidates-v2',b'changed',media_type='application/json')
        else:
            event=payload['events'][0];event['data']['command_seq']+=1
            builder.add_event(event['event_id'],event['kind'],event['data'])

@pytest.mark.parametrize('kind',['CandidateSelection','ControlSnapshot'])
def test_missing_record_rejected(archive,kind):
    payload=archive.to_dict();payload['records']=[r for r in payload['records'] if r['record_type']!=kind]
    with pytest.raises((RegistryError,ArchiveError),match='missing_reference|incomplete_record'):
        RunArchive.from_bytes(envelope(payload))

@pytest.mark.parametrize('kind',['CandidateSet','SessionConfig','SceneGeometry','TaskContext','SemanticContext','JoystickSample','RobotState','ControlResult'])
def test_missing_message_rejected(archive,kind):
    payload=archive.to_dict();payload['messages']=[r for r in payload['messages'] if r['message_type']!=kind]
    with pytest.raises((RegistryError,ArchiveError),match='missing_reference|incomplete_record'):
        RunArchive.from_bytes(envelope(payload))

@pytest.mark.parametrize('identifier',['numeric-candidates-v2','solver-candidates-v2','intent-candidates-v2','candidates-reference-v2',
    'synthetic-rotary-v1','synthetic-host-v1','fixture-installation-v2','archive-scenario-v2'])
def test_missing_exact_resource_rejected(archive,identifier):
    payload=archive.to_dict();payload['resources']=[r for r in payload['resources'] if r['resource_id']!=identifier]
    with pytest.raises(ArchiveError,match='missing_reference'): RunArchive.from_bytes(envelope(payload))

def test_old_archive_is_explicitly_rejected_without_modifying_it():
    path=ROOT/'tests/fixtures/shared_control/control_run_v1.json'; before=path.read_bytes()
    with pytest.raises(ArchiveError,match='unsupported_version'): RunArchive.read(path)
    assert path.read_bytes()==before

def test_checksum_and_schema_are_enforced(archive):
    data=json.loads(archive.to_bytes());data['payload']['manifest']['run_id']='changed'
    with pytest.raises(ArchiveError,match='checksum_mismatch'): RunArchive.from_bytes(encode(data))
    payload=archive.to_dict();payload['manifest']['schema_version']='1.0'
    with pytest.raises(ArchiveError,match='unsupported_contract'): RunArchive.from_bytes(envelope(payload))
    with pytest.raises(ArchiveError,match='checksum_mismatch'): RunArchive.from_bytes(archive.to_bytes(),expected_sha256='0'*64)

def test_distorted_selection_is_detected_by_replay(archive):
    payload=archive.to_dict()
    selection=next(i['data'] for i in payload['records'] if i['record_type']=='CandidateSelection')
    selection['scores'][0]['score']+=.2
    report=replay_archive(RunArchive.from_bytes(envelope(payload)))
    assert not report['ok'] and not report['selections'][0]['ok']

def test_distorted_fixed_distance_is_detected_by_replay(archive):
    payload=archive.to_dict()
    result=next(i['payload'] for i in payload['messages'] if i['message_type']=='ControlResult')
    result['candidate_evaluation']['raw_g']+=.001
    report=replay_archive(RunArchive.from_bytes(envelope(payload)))
    assert not report['ok'] and not report['evaluations'][0]['ok']

def test_missing_evaluation_and_invalid_selection_reference_rejected(archive):
    payload=archive.to_dict();payload['events']=[e for e in payload['events'] if e['kind']!='evaluation']
    with pytest.raises(ArchiveError,match='incomplete_record'): RunArchive.from_bytes(envelope(payload))
    payload=archive.to_dict()
    result=next(i['payload'] for i in payload['messages'] if i['message_type']=='ControlResult')
    result['candidate_evaluation']['candidate_id']='candidate-cup-v2'
    with pytest.raises(ArchiveError,match='incompatible_reference'): RunArchive.from_bytes(envelope(payload))

def test_exact_solver_resource_is_required(archive):
    payload=archive.to_dict()
    import base64
    item=next(r for r in payload['resources'] if r['resource_id']=='solver-candidates-v2')
    config=json.loads(base64.b64decode(item['base64']));config['distance_scale_m']=.02
    data=encode(config);item['base64']=base64.b64encode(data).decode();item['sha256']=sha256(data).hexdigest()
    with pytest.raises(ArchiveError,match='unsupported_configuration'): RunArchive.from_bytes(envelope(payload))

def test_acceptance_must_precede_referencing_snapshot(archive):
    payload=archive.to_dict()
    acceptance=next(e for e in payload['events'] if e['kind']=='acceptance')
    acceptance['data']['checked_at']['sec']=20
    with pytest.raises(ArchiveError,match='invalid_timing'): RunArchive.from_bytes(envelope(payload))

def test_cli_accepts_new_and_rejects_old(archive,tmp_path):
    path=tmp_path/'run.json';archive.write(path)
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'))
    completed=subprocess.run([sys.executable,'-m','ssc_core.replay',str(path)],env=env,capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)['ok']
    old=subprocess.run([sys.executable,'-m','ssc_core.replay',str(ROOT/'tests/fixtures/shared_control/control_run_v1.json')],
                       env=env,capture_output=True,text=True)
    assert old.returncode==2 and json.loads(old.stderr)['code']=='unsupported_version'
