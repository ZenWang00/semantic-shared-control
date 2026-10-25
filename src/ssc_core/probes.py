"""Deterministic numeric-offline-v1 poses; no generated source is run here."""

from dataclasses import dataclass
import math

from .records import Matrix3, Vector3
from .registry import ResolvedProgram


IDENTITY: Matrix3 = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
ORIGIN: Vector3 = (.4, 0., .3)


@dataclass(frozen=True, slots=True)
class ValidationProbe:
    name: str
    position: Vector3
    rotation: Matrix3


def _rotation(axis: int, angle: float) -> Matrix3:
    c, s = math.cos(angle), math.sin(angle)
    return (
        ((1., 0., 0.), (0., c, -s), (0., s, c)),
        ((c, 0., s), (0., 1., 0.), (-s, 0., c)),
        ((c, -s, 0.), (s, c, 0.), (0., 0., 1.)),
    )[axis]


def _align_x(direction: Vector3) -> Matrix3:
    # atan2 remains well-conditioned for nearly parallel/antiparallel axes.
    x, y, z = direction
    sine = math.hypot(y, z)
    if sine == 0:
        return IDENTITY if x > 0 else _rotation(2, math.pi)
    angle = math.atan2(sine, x)
    c, s = math.cos(angle), math.sin(angle)
    v = (0., -z / sine, y / sine)
    skew = ((0., -v[2], v[1]), (v[2], 0., -v[0]), (-v[1], v[0], 0.))
    return tuple(tuple(c * (i == j) + (1 - c) * v[i] * v[j] + s * skew[i][j]
                       for j in range(3)) for i in range(3))


def validation_probes(resolved: ResolvedProgram) -> tuple[ValidationProbe, ...]:
    """49 ordered poses plus bound targets and directed-axis opposition.

    Every bound keypoint is probed at identity, including approach targets.
    Empty bindings stay empty; no zero-filled substitute geometry is invented.
    Axis reversal is performed by the host validator at every returned pose.
    """
    positions = [("origin", ORIGIN)]
    rotations = [("identity", IDENTITY)]
    for axis, label in enumerate("xyz"):
        for sign, suffix in ((1, "+"), (-1, "-")):
            position = list(ORIGIN)
            position[axis] += sign * .01
            positions.append((label + suffix, tuple(position)))
            rotations.append((label + suffix, _rotation(axis, sign * .1)))
    result = [ValidationProbe(f"grid/{pname}/{rname}", position, rotation)
              for pname, position in positions for rname, rotation in rotations]
    for key, position in zip(resolved.program.keypoint_bindings, resolved.keypoints):
        result.append(ValidationProbe(f"keypoint/{key}", position, IDENTITY))
    axes = {axis.axis_id: axis for axis in resolved.geometry.axes}
    for key, direction in zip(resolved.program.axis_bindings, resolved.axis_dirs):
        if axes[key].directed:
            for sign, suffix in ((1, "aligned"), (-1, "opposed")):
                result.append(ValidationProbe(f"axis/{key}/{suffix}", ORIGIN,
                                             _align_x(tuple(sign * v for v in direction))))
    return tuple(result)
