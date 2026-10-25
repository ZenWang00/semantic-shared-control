"""Host normalization settings and finite residual diagnostics (no authority)."""

from dataclasses import dataclass
import math

from .records import Unit


def _finite(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite non-Boolean number")
    return float(value)


@dataclass(frozen=True, slots=True)
class HostNormalization:
    """Local host configuration; unit is never added to the wire binding."""

    unit: Unit
    scale: float
    weight: float = 1.

    def __post_init__(self):
        if self.unit not in ("m", "rad", "dimensionless"):
            raise ValueError("unsupported normalization unit")
        if _finite(self.scale, "scale") <= 0:
            raise ValueError("scale must be positive")
        if _finite(self.weight, "weight") < 0:
            raise ValueError("weight must be nonnegative")


@dataclass(frozen=True, slots=True)
class ResidualDiagnostic:
    raw_residual: float
    output_unit: Unit
    normalization_scale: float
    penalty: float
    weight: float
    weighted_penalty: float
    relation_satisfied: bool


def residual_diagnostic(g: float, *, output_unit: Unit,
                        normalization: HostNormalization) -> ResidualDiagnostic:
    """Preserve g and max(0, g/s)^2; a positive finite g is valid execution.

    ``relation_satisfied`` uses numeric-offline-v1's 1e-9 raw tolerance;
    neither that flag nor a zero penalty grants motion authority.
    """
    if type(normalization) is not HostNormalization:
        raise TypeError("expected host normalization settings")
    if output_unit != normalization.unit:
        raise ValueError("normalization unit differs from program output unit")
    raw = _finite(g, "residual")
    # Hinge before division avoids overflow for large negative finite residuals.
    normalized = max(0., raw) / normalization.scale
    penalty = normalized * normalized
    weighted = penalty * normalization.weight
    if not math.isfinite(penalty) or not math.isfinite(weighted):
        raise ValueError("normalized penalty is nonfinite")
    return ResidualDiagnostic(raw, output_unit, normalization.scale, penalty,
                              normalization.weight, weighted, raw <= 1e-9)
