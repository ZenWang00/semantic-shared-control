"""Private host-checker doubles for server tests and evidence scripts only.

These are NOT synthetic-host-v1 and grant nothing. They let the shared solver be
tested through its real ``module:attribute`` injection path without importing
the robot repository. ``WorkspaceSphereDouble`` reproduces only the profile's
workspace box and excluded-sphere segment margins; it has no model, joint or
feedback checks. Consumers inject their own checker (for example
``ssc_runtime.constraints:SyntheticHostChecker``).
"""
from dataclasses import dataclass
import math

from .host_constraints import ConstraintMargin, HostConstraintResult
from .policy import NUMERIC_CANDIDATES_V2

_LOWER, _UPPER, _SCALE = (.3, -.1, .2), (.5, .1, .4), .2
_CENTER, _CLEARANCE = (.45, .07, .30), .0201


def _segment_distance(start, end, center):
    delta = [b - a for a, b in zip(start, end)]
    squared = math.fsum(d * d for d in delta)
    fraction = 0.0
    if squared:
        fraction = min(1.0, max(0.0, math.fsum((c - a) * d for a, c, d in zip(start, center, delta)) / squared))
    return math.dist([a + fraction * d for a, d in zip(start, delta)], center)


@dataclass(frozen=True, slots=True)
class WorkspaceSphereDouble:
    """Workspace AABB and excluded-sphere path margins; frames must match."""

    def __call__(self, *, candidate_pose, measured_state, u_xy, dt_s, configuration):
        if (measured_state.frame_id, measured_state.tcp_frame) != (configuration.control_frame,
                                                                   configuration.tcp_frame):
            raise ValueError("frame_mismatch")
        position = tuple(float(v) for v in candidate_pose.position_xyz)
        margins = []
        for axis, value, lower, upper in zip("xyz", position, _LOWER, _UPPER):
            margins.append(ConstraintMargin(f"workspace_{axis}_lower", (value - lower) / _SCALE, f"workspace {axis} lower"))
            margins.append(ConstraintMargin(f"workspace_{axis}_upper", (upper - value) / _SCALE, f"workspace {axis} upper"))
        start = tuple(float(v) for v in measured_state.tcp_pose.position_xyz)
        distance = _segment_distance(start, position, _CENTER)
        margins.append(ConstraintMargin("excluded_sphere_segment", (distance - _CLEARANCE) / _SCALE,
                                        "path enters excluded sphere"))
        return HostConstraintResult(tuple(margins), policy=NUMERIC_CANDIDATES_V2)


@dataclass(frozen=True, slots=True)
class RaisingDouble:
    """Always raises: a checker error must reject, never become feasible."""

    def __call__(self, **_):
        raise RuntimeError("injected checker failure")


@dataclass(frozen=True, slots=True)
class NotACheckerResult:
    """Returns a plain tuple instead of HostConstraintResult."""

    def __call__(self, **_):
        return ((("margin", 1.0),))
