"""Pure consumer-owned constraint callback; no gate or robot implementation.

Inputs are already validated, pinned values. The later coordinator supplies live
RobotState data as MeasuredStateView and the gate independently rechecks current
authority/freshness. Callback exceptions or invalid reports must reject a
candidate; callers must not replace failures with a feasible result.
"""

from dataclasses import dataclass, field
import math
from typing import Protocol

from .errors import ValidationError
from .policy import ValidationPolicy
from .records import Pose, SessionConfig

Number = int | float


@dataclass(frozen=True, slots=True)
class MeasuredStateView:
    """Read-only value view, not a validated RobotState or provenance claim.

    Create this from the later live-record validation boundary. Tuples and a
    parsed Pose are required; no clocks, devices or source objects are retained.
    """

    frame_id: str
    tcp_frame: str
    tcp_pose: Pose
    joint_names: tuple[str, ...]
    joint_positions_rad: tuple[Number, ...]

    def __post_init__(self) -> None:
        if type(self.tcp_pose) is not Pose:
            raise TypeError("tcp_pose must be a parsed Pose")
        if type(self.joint_names) is not tuple or type(self.joint_positions_rad) is not tuple:
            raise TypeError("joint data must use immutable tuples")
        if any(type(name) is not str for name in self.joint_names):
            raise TypeError("joint names must be strings")
        if any(type(value) not in (int, float) for value in self.joint_positions_rad):
            raise TypeError("joint positions must be numbers, not booleans")


@dataclass(frozen=True, slots=True)
class ConstraintMargin:
    """A named dimensionless margin and diagnostic if it violates the bound."""

    name: str
    value: Number
    violation_reason: str

    def __post_init__(self) -> None:
        for key in ("name", "violation_reason"):
            value = getattr(self, key)
            if type(value) is not str or not value.strip():
                raise ValidationError(f"$.{key}", "required_text", "expected nonblank text")
        try:
            valid = type(self.value) in (int, float) and math.isfinite(self.value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValidationError("$.value", "finite_number", "margin must be a finite number")


@dataclass(frozen=True, slots=True)
class HostConstraintResult:
    """Immutable mathematical result; feasibility cannot be supplied separately.

    An empty report is invalid: a missing checker is not evidence of feasibility.
    Reasons are derived from the same failed margins as the feasibility flag.
    This does not assert robot-side command acceptance or physical safety.
    """

    margins: tuple[ConstraintMargin, ...]
    policy: ValidationPolicy = field(kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if type(self.policy) is not ValidationPolicy:
            raise TypeError("policy must be an explicit ValidationPolicy")
        if type(self.margins) is not tuple or not self.margins:
            raise ValidationError("$.margins", "shape", "expected a nonempty tuple of margins")
        if any(type(margin) is not ConstraintMargin for margin in self.margins):
            raise ValidationError("$.margins", "type", "expected ConstraintMargin values")
        if len({margin.name for margin in self.margins}) != len(self.margins):
            raise ValidationError("$.margins", "duplicate", "margin names must be unique")

    @property
    def feasible(self) -> bool:
        return all(margin.value >= -self.policy.hard_margin_tolerance for margin in self.margins)

    @property
    def violation_reasons(self) -> tuple[str, ...]:
        return tuple(
            margin.violation_reason for margin in self.margins
            if margin.value < -self.policy.hard_margin_tolerance
        )


class HostConstraintChecker(Protocol):
    """Consumer implements model/workspace/path margins using immutable data.

    configuration identifies the exact model and constraint set. Any additional
    model constants must be immutable values owned by the checker implementation,
    not fetched from devices, clocks, files, networks or mutable globals. This
    interface supplies no basic step margins, solver, command gate or controller.
    """

    def __call__(
        self,
        *,
        candidate_pose: Pose,
        measured_state: MeasuredStateView,
        u_xy: tuple[Number, Number],
        dt_s: Number,
        configuration: SessionConfig,
    ) -> HostConstraintResult: ...
