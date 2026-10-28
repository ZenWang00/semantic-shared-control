"""Explicit immutable policy for candidate data and fixed numeric control."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    policy_id: str
    unit_norm_tolerance: float = 1e-8
    hard_margin_tolerance: float = 1e-8
    max_candidate_objects: int = 16
    max_candidates: int = 16
    rotation_tolerance: float = 1e-8

    def __post_init__(self) -> None:
        if type(self.policy_id) is not str or not self.policy_id:
            raise ValueError("policy_id must be a nonempty string")
        for name in ("unit_norm_tolerance", "hard_margin_tolerance", "rotation_tolerance"):
            value = getattr(self, name)
            try:
                valid = type(value) in (int, float) and isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("max_candidate_objects", "max_candidates"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")


NUMERIC_CANDIDATES_V2 = ValidationPolicy(policy_id="numeric-candidates-v2")
