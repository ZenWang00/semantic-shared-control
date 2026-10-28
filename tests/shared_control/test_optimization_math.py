"""Analytical oracles for three-variable candidate position assistance."""
import math
import time
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from ssc_core import _solver_math as m
from ssc_core.optimization import _relative_rotvec, basic_step_margins
from ssc_core.records import Pose

L = .01
ORIGIN = (.4, 0., .3)
def problem(u=(1.,0.),alpha=.5,target=(.41,0.,.3),prev=(0.,0.,0.),**extra):
    data = {'p_tcp': list(ORIGIN), 'q_tcp': [0.,0.,0.,1.], 'L_m':L,'xy_gain':1.,'u_xy':list(u),
            'alpha':alpha,'smoothing':.1,'x_prev':list(prev),'candidate_id':'candidate-test',
            'candidate_position_xyz':list(target),'distance_scale_m':L,'distance_tolerance_m':0.,
            'hard_margin_tolerance':1e-8}
    data.update(extra)
    return data

def free(p,q): return ['free'],[1.]
def solve(data,host=free): return m.solve_problem(data,host,deadline_ns=time.monotonic_ns()+5_000_000_000)
def delta(report): return np.asarray(report['position_xyz']) - ORIGIN

def test_joystick_only_analytic_oracle():
    report=solve(problem(alpha=0))
    assert delta(report)==pytest.approx((10*L/11,0,0),abs=1e-7)
    assert report['terms']['assistance']==0

def test_selected_target_analytic_oracle():
    report=solve(problem())
    assert delta(report)==pytest.approx((15*L/16,0,0),abs=1e-7)
    assert report['candidate_evaluation']['raw_g']==pytest.approx(L/16,abs=1e-7)
    assert report['candidate_evaluation']['penalty']==pytest.approx(1/256,abs=1e-5)

@pytest.mark.parametrize('height,sign',[(.31,1),(.3,0),(.29,-1)])
def test_same_xy_input_supplements_height_from_target(height,sign):
    report=solve(problem(target=(.41,0,height)))
    dz=delta(report)[2]
    assert abs(dz)<1e-7 if sign==0 else sign*dz>1e-3
    assert np.linalg.norm(delta(report)) <= L+1e-9

@pytest.mark.parametrize('sign',[1.,-1.])
def test_nonidentity_orientation_is_copied_including_quaternion_sign(sign):
    q=(sign*Rotation.from_rotvec((.7,-.3,.2)).as_quat()).tolist()
    report=solve(problem(target=(.41,0,.31),q_tcp=q))
    assert report['quaternion_xyzw']==q

def test_no_assistance_has_exact_zero_z_even_with_previous_z():
    report=solve(problem(alpha=0,target=(.41,0,.39),prev=(.2,0,.8)))
    assert report['position_xyz'][2]==ORIGIN[2]
    assert report['quaternion_xyzw']==[0.,0.,0.,1.]
    assert delta(report)[0]==pytest.approx(1.02*L/1.1,abs=1e-7)

def test_reversal_is_immediate_despite_history():
    report=solve(problem(u=(-1.,0.),target=(.39,0,.3),prev=(1.,0,0)))
    assert delta(report)==pytest.approx((-1.4*L/1.6,0,0),abs=1e-7)

def test_target_does_not_latch_and_input_can_move_past_it():
    report=solve(problem(target=ORIGIN))
    assert delta(report)[0]==pytest.approx(L/1.6,abs=1e-7)

def test_half_space_forbids_opposing_step():
    report=solve(problem(target=(.35,0,.3)))
    assert delta(report)[0]>=-1e-10

def test_host_margin_limits_motion():
    report=solve(problem(),lambda p,q:(['wall'],[(.404-p[0])/.2]))
    assert delta(report)[0]==pytest.approx(.004,abs=1e-7)
    assert dict(report['host_margins'])['wall']>=-1e-8

def test_objective_is_fixed_unweighted_distance():
    x=np.array([.5,.5,0.])
    data=problem(u=(.6,.8),prev=(.1,0,0))
    tracking,smoothing,assistance=m.objective_terms(data,x,{'penalty':.25})
    assert tracking==pytest.approx(.1**2+.3**2)
    assert smoothing==pytest.approx(.1*(.4**2+.5**2))
    assert assistance==pytest.approx(.125)

def test_emitted_translation_margins_match_host():
    data=problem(u=(.3,-.4)); x=np.array([.2,-.1,.05])
    p,q=m.candidate_pose(data,x)
    host=basic_step_margins(Pose(tuple(p),tuple(q)),Pose(ORIGIN,(0,0,0,1)),data['u_xy'],step_m=L,step_rad=.1)
    for name,value in m.basic_margins(data,m.emitted_increment(data,p)).items():
        assert host[name]==pytest.approx(value,abs=1e-12)

@pytest.mark.parametrize('rotvec',[(0,0,0),(.1,-.2,.05),(0,0,math.pi-1e-6),(1e-9,0,0)])
def test_host_rotation_check_treats_quaternion_sign_as_equivalent(rotvec):
    source=Rotation.from_rotvec((.3,.2,-.1)); target=Rotation.from_rotvec(rotvec)*source
    assert _relative_rotvec(target.as_quat(),-source.as_quat())==pytest.approx(rotvec,abs=1e-9)

def test_expired_deadline_and_nonfinite_objective_fail_closed():
    with pytest.raises(m.SolveFailure) as error:
        m.solve_problem(problem(),free,deadline_ns=time.monotonic_ns()-1)
    assert error.value.status=='timeout'
    with pytest.raises(m.SolveFailure) as error:
        solve(problem(target=(1e300,0,0)))
    assert error.value.code=='nonfinite_objective'

def test_infeasible_margin_is_not_partial_solution():
    with pytest.raises(m.SolveFailure) as error:
        solve(problem(),lambda p,q:(['impossible'],[-1.]))
    assert error.value.status in ('infeasible','timeout')
