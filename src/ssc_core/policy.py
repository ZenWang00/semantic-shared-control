"""Explicit, immutable host policy for validation and restricted evaluation."""

from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Host-selected limits; record metadata does not select or override them.

    These are the limits used by this package's structural checks and by the
    restricted residual interpreter's language and numerical checks. Process
    memory/time budgets, per-snapshot program counts and other runtime budgets
    belong to their respective later layers.
    """

    policy_id: str
    unit_norm_tolerance: float
    hard_margin_tolerance: float
    max_source_bytes: int
    max_candidate_objects: int
    max_keypoint_bindings: int
    max_axis_bindings: int
    max_parameters: int
    weight_sum_tolerance: float = 1e-12
    rotation_tolerance: float = 1e-8
    max_ast_nodes: int = 512
    max_ast_depth: int = 32
    max_assignments: int = 64
    max_array_elements: int = 256

    def __post_init__(self) -> None:
        if type(self.policy_id) is not str or not self.policy_id:
            raise ValueError("policy_id must be a nonempty string")
        for name in ("unit_norm_tolerance", "hard_margin_tolerance", "weight_sum_tolerance",
                     "rotation_tolerance"):
            value = getattr(self, name)
            try:
                valid = type(value) in (int, float) and isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{name} must be finite and nonnegative")
        for field in fields(self):
            if field.name.startswith("max_"):
                value = getattr(self, field.name)
                if type(value) is not int or value < 1:
                    raise ValueError(f"{field.name} must be a positive integer")


NUMERIC_OFFLINE_V1 = ValidationPolicy(
    policy_id="numeric-offline-v1",
    unit_norm_tolerance=1e-8,
    hard_margin_tolerance=1e-8,
    max_source_bytes=8192,
    max_candidate_objects=8,
    max_keypoint_bindings=32,
    max_axis_bindings=32,
    max_parameters=32,
    weight_sum_tolerance=1e-12,
    rotation_tolerance=1e-8,
    max_ast_nodes=512,
    max_ast_depth=32,
    max_assignments=64,
    max_array_elements=256,
)
