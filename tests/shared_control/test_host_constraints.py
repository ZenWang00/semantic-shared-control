"""Exercise the callback boundary without a robot, coordinator, or real model."""

from dataclasses import FrozenInstanceError, dataclass, replace
import json
import math
from pathlib import Path

import pytest

from ssc_core import NUMERIC_OFFLINE_V1, ValidationError, parse_record
from ssc_core.host_constraints import (
    ConstraintMargin, HostConstraintChecker, HostConstraintResult, MeasuredStateView,
)


@dataclass(frozen=True)
class PlaneChecker:
    """Local double: x <= limit. This is deliberately not the synthetic checker."""

    limit: float

    def __call__(self, *, candidate_pose, measured_state, u_xy, dt_s, configuration):
        assert measured_state.frame_id == configuration.control_frame
        assert measured_state.tcp_frame == configuration.tcp_frame
        assert u_xy == (1, 0)
        assert dt_s == 0.2
        return HostConstraintResult(
            (ConstraintMargin("plane", self.limit - candidate_pose.position_xyz[0], "outside plane"),),
            policy=NUMERIC_OFFLINE_V1,
        )


def test_consumer_owned_checker_uses_explicit_values():
    # The unchanged catalog gives realistic config fields without a ROS2 import.
    source = Path(__file__).resolve().parents[1] / "fixtures/shared_control/bottle_cup_illustrative.json"
    catalog = json.loads(source.read_text())
    raw = next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SessionConfig")
    config = parse_record("SessionConfig", raw, policy=NUMERIC_OFFLINE_V1)
    pose = parse_record("Pose", {"position_xyz": [0.4, 0, 0.3], "quaternion_xyzw": [0, 0, 0, 1]}, policy=NUMERIC_OFFLINE_V1)
    measured = MeasuredStateView(config.control_frame, config.tcp_frame, pose, ("q1",), (0,))
    checker: HostConstraintChecker = PlaneChecker(0.5)
    result = checker(candidate_pose=pose, measured_state=measured, u_xy=(1, 0), dt_s=0.2, configuration=config)
    assert result.feasible is True
    assert result.violation_reasons == ()
    forbidden = parse_record("Pose", {"position_xyz": [0.6, 0, 0.3], "quaternion_xyzw": [0, 0, 0, 1]}, policy=NUMERIC_OFFLINE_V1)
    result = checker(candidate_pose=forbidden, measured_state=measured, u_xy=(1, 0), dt_s=0.2, configuration=config)
    assert result.feasible is False
    assert result.violation_reasons == ("outside plane",)
    with pytest.raises(FrozenInstanceError):
        measured.frame_id = "other"
    assert measured.tcp_pose.position_xyz == (0.4, 0, 0.3)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "0", 10**400])
def test_invalid_margin_cannot_be_a_feasible_report(value):
    with pytest.raises(ValidationError):
        HostConstraintResult((ConstraintMargin("bad", value, "bad margin"),), policy=NUMERIC_OFFLINE_V1)


def test_reference_tolerance_and_derived_decisions():
    limit = -1e-8
    for value, feasible in [(0, True), (limit, True), (math.nextafter(limit, -math.inf), False)]:
        result = HostConstraintResult((ConstraintMargin("bound", value, "violated"),), policy=NUMERIC_OFFLINE_V1)
        assert result.feasible is feasible
        assert result.violation_reasons == (() if feasible else ("violated",))
    with pytest.raises(TypeError):
        HostConstraintResult((ConstraintMargin("bound", -1, "violated"),), policy=NUMERIC_OFFLINE_V1, feasible=True)


def test_missing_duplicate_and_mutable_margin_reports_rejected():
    margin = ConstraintMargin("bound", 1, "violated")
    for margins in [(), [margin], (margin, margin)]:
        with pytest.raises(ValidationError):
            HostConstraintResult(margins, policy=NUMERIC_OFFLINE_V1)
    with pytest.raises(FrozenInstanceError):
        margin.value = -1
    with pytest.raises(TypeError):
        HostConstraintResult((margin,))


def test_callback_error_is_not_replaced_by_feasibility():
    def broken_checker(**kwargs):
        raise RuntimeError("unavailable model")

    with pytest.raises(RuntimeError, match="unavailable model"):
        broken_checker()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 10**400])
def test_invalid_host_policy_cannot_relax_margin_comparison(value):
    with pytest.raises(ValueError):
        replace(NUMERIC_OFFLINE_V1, hard_margin_tolerance=value)
