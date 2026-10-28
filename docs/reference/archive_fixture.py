"""Build synthetic v2 data-only replay evidence; no controller or optimizer run.

The caller supplies installed server revision and wheel digest. The consumer
entry identifies a historical reference only, never an executed version pair.
"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import runpy

from ssc_core import NUMERIC_CANDIDATES_V2, SessionRegistry, estimate_intent, get_intent_config, get_solver_config, to_dict
from ssc_core.archive import ArchiveBuilder, environment

ROOT=Path(__file__).resolve().parents[2]

def stamp(ns):
    sec,nano=divmod(ns,1_000_000_000)
    return {'clock_id':'synthetic-clock-offline-v1','sec':sec,'nanosec':nano}

def build_fixture(*,source_revision,wheel_sha256):
    env=environment()
    manifest={'run_id':'archive-fixed-fixture-v2','session_id':'session-offline-v1','schema_version':'2.0',
        'candidate_api_version':'candidate-points-v2','profile_id':'candidates-reference-v2',
        'validation_policy_id':'numeric-candidates-v2','solver_config_id':'solver-candidates-v2',
        'installation_id':'fixture-installation-v2','scenario_id':'archive-scenario-v2','environment':env,
        'software':{'server':{'revision':source_revision,'package_version':env['packages']['ssc-core'],'wheel_sha256':wheel_sha256},
                    'ros2':{'revision':'4ea685cdc46ca6857462c5e25b0c8d53a1154db2','package_version':'0.5.0',
                            'wheel_sha256':'444056500e64b5d91e357d5b8aa9756b1fe6e966b57253e5f01b72cc1b39dcf9'}},
        'evidence_scope':'Synthetic fixed archive: real candidate selection, analytical proposals and scripted decisions. '
                         'Consumer provenance is a historical reference only; no executed consumer, robot or VLM.',
        'replay_tolerances':{'atol':1e-9,'rtol':1e-9}}
    writer=ArchiveBuilder(manifest); registry=SessionRegistry(manifest['session_id'],policy=NUMERIC_CANDIDATES_V2)
    def resource(key,value):
        writer.add_resource(key,json.dumps(value,sort_keys=True).encode(),media_type='application/json')
    def record(kind,data):
        parsed=registry.register_record(kind,data,session_id=registry.session_id)
        writer.add_record(kind,parsed); return parsed
    def message(kind,payload,key,seq,ns):
        data={'message_type':kind,'payload':payload,'meta':{'schema_version':'2.0','session_id':registry.session_id,
              'message_id':key,'source_id':'archive-fixture-'+kind,'sequence':seq,'produced_at':stamp(ns)}}
        writer.add_message(data); return registry.register_message(data)
    catalog=json.loads((ROOT/'tests/fixtures/shared_control/bottle_cup_candidates_v2.json').read_text())
    artifacts={item['message_type']:item['payload'] for item in catalog['messages']}
    for kind,payload in artifacts.items(): message(kind,payload,'archive-'+kind,0,10_000_000_000)
    config,geometry,semantic,task,candidates=[artifacts[k] for k in
        ('SessionConfig','SceneGeometry','SemanticContext','TaskContext','CandidateSet')]
    for key in (manifest['profile_id'],manifest['installation_id'],manifest['scenario_id'],config['robot_model_id'],
                config['motion_constraint_set_id'],'synthetic-intent-mapping'):
        resource(key,{'scope':'synthetic data-only archive fixture','identifier':key,'manifest':manifest if key==manifest['installation_id'] else None})
    resource('numeric-candidates-v2',asdict(NUMERIC_CANDIDATES_V2))
    resource('solver-candidates-v2',asdict(get_solver_config()))
    resource('intent-candidates-v2',asdict(get_intent_config('intent-candidates-v2')))
    observation=artifacts['SceneObservation']
    resource(observation['calibration_id'],{'K':observation['K'],'transform':observation['T_control_camera']})
    for kind in ('rgb_ref','depth_ref'):
        writer.add_resource(observation[kind]['asset_id'],b'synthetic placeholder; no captured pixels',media_type=observation[kind]['media_type'])
    previous_selection=None
    for i,u in enumerate(((1,0),(-1,0),(0,0))):
        ns=10_000_000_000+i*200_000_000
        joystick=message('JoystickSample',{'sampled_at':stamp(ns),'mapping_id':'synthetic-intent-mapping',
            'u_xy':list(u),'enabled':True},f'joystick-{i}',i,ns)
        state=message('RobotState',{'sampled_at':stamp(ns),'frame_id':config['control_frame'],'tcp_frame':config['tcp_frame'],
            'tcp_pose':catalog['initial_pose'],'joint_names':catalog['initial_joint_names'],
            'joint_positions_rad':catalog['initial_joint_positions_rad'],'state_source':'fk_from_measured_joints'},f'state-{i}',i,ns)
        snapshot=record('ControlSnapshot',{'snapshot_id':f'snapshot-{i}','snapshot_seq':i,'control_epoch':0,
            'config_id':config['config_id'],'task_ref':{'task_id':task['task_id'],'revision':task['revision']},'dt_s':.2,
            'geometry_ref':{'geometry_id':geometry['geometry_id'],'revision':geometry['revision']},'semantic_id':semantic['semantic_id'],
            'candidate_set_id':candidates['candidate_set_id'],'joystick_ref':joystick.meta.message_id,
            'robot_state_ref':state.meta.message_id,'joystick_history_refs':[],
            'previous_selection_id':previous_selection,'previous_applied_command_id':f'applied-{i-1}' if i else None})
        resolved=registry.resolve_snapshot(snapshot.snapshot_id)
        selection=estimate_intent(resolved,get_intent_config('intent-candidates-v2'),selection_id=f'selection-{i}',policy=NUMERIC_CANDIDATES_V2).selection
        record('CandidateSelection',selection)
        previous_selection=selection.selection_id if selection.selected_candidate_id else None
        writer.add_event(f'dispatch-{i}','dispatch',{'snapshot_id':snapshot.snapshot_id,'command_seq':i,
            'created_at':stamp(ns),'dispatched_at':stamp(ns+1_000_000),'expires_at':stamp(ns+200_000_000),
            'solve_deadline':stamp(ns+151_000_000),'inputs':[{'message_id':m.meta.message_id,'received_at':stamp(ns),'source_age_ns':0}
                                                         for m in (joystick,state)]})
        result={'result_id':f'result-{i}','snapshot_id':snapshot.snapshot_id,'selection_id':selection.selection_id,'control_epoch':0,
            'command_seq':i,'source_joystick_seq':i,'source_robot_state_seq':i,'status':'hold','evaluation_valid':False,
            'solver_status':'not_run','constraints_satisfied':None,'solve_time_s':0,'candidate_evaluation':None,'reason':'scripted release'}
        if selection.selected_candidate_id:
            point=next(p for p in resolved.bundle.candidates if p.candidate_id==selection.selected_candidate_id)
            pose={'position_xyz':[.4+u[0]*.005,0,.3],'quaternion_xyzw':[0,0,0,1]}
            distance=math.dist(pose['position_xyz'],point.position_xyz)
            result.update(status='move',evaluation_valid=True,solver_status='solved',constraints_satisfied=False,solve_time_s=.01,
                candidate_evaluation={'candidate_id':point.candidate_id,'distance_m':distance,'raw_g':distance,
                                      'normalization_scale':.01,'penalty':(distance/.01)**2},next_tcp_pose=pose,horizon_s=.2)
            result.pop('reason')
        message('ControlResult',result,f'message-result-{i}',i,ns+10_000_000)
        if selection.selected_candidate_id:
            writer.add_event(f'evaluation-{i}','evaluation',{'result_id':f'result-{i}','pose':pose})
        writer.add_event(f'acceptance-{i}','acceptance',{'result_id':f'result-{i}','command_accepted':True,
            'reason':'scripted fixture acceptance','checked_at':stamp(ns+11_000_000),'applied_command_id':f'applied-{i}'})
    return writer.seal()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-revision',required=True); parser.add_argument('--wheel-sha256',required=True)
    parser.add_argument('--output',required=True); args=parser.parse_args()
    archive=build_fixture(source_revision=args.source_revision,wheel_sha256=args.wheel_sha256)
    archive.write(args.output); print(json.dumps({'path':args.output,'sha256':archive.sha256}))

if __name__=='__main__': main()
