"""Dependency-free scene/artifact contracts for semantic shared control.

Parsing validates structure and local invariants only. It does not authorize
source execution, accept a semantic bundle, or certify a robot command.
"""

from .errors import ValidationError
from .policy import NUMERIC_OFFLINE_V1, ValidationPolicy
from .records import (
    AssetRef, Axis, ConstraintProgram, GeometryRef, Keypoint, Matrix3, Message,
    MessageMeta, Number, Object, ObjectRelevance, Parameter, Payload, Pose,
    ProgramExecutionBinding, ProgramRef, ProgramValidationReceipt, Quaternion,
    Record, SceneGeometry, SceneObservation, SemanticContext, SessionConfig,
    StampedTransform, TaskContext, TimePoint, Unit, Vector3,
)
from .validation import loads_message, parse_message, parse_record, to_dict
from .host_constraints import (
    ConstraintMargin, HostConstraintChecker, HostConstraintResult, MeasuredStateView,
)

__version__ = "0.1.0"

__all__ = [
    "AssetRef", "Axis", "ConstraintMargin", "ConstraintProgram", "GeometryRef",
    "HostConstraintChecker", "HostConstraintResult", "Keypoint", "Matrix3",
    "MeasuredStateView", "Message", "MessageMeta", "NUMERIC_OFFLINE_V1", "Number",
    "Object", "ObjectRelevance", "Parameter", "Payload", "Pose",
    "ProgramExecutionBinding", "ProgramRef", "ProgramValidationReceipt",
    "Quaternion", "Record", "SceneGeometry", "SceneObservation", "SemanticContext",
    "SessionConfig", "StampedTransform", "TaskContext", "TimePoint", "Unit",
    "ValidationError", "ValidationPolicy", "Vector3", "loads_message",
    "parse_message", "parse_record", "to_dict",
]
