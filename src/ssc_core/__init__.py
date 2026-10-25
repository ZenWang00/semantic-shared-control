"""Dependency-free structural contracts for semantic shared control.

Parsing validates structure and local invariants only. It does not authorize
source execution, accept a semantic bundle, or certify a robot command.
"""

from .errors import ValidationError
from .policy import NUMERIC_OFFLINE_V1, ValidationPolicy
from .records import (
    AssetRef, Axis, CommandAcceptanceRecord, ConstraintEvaluation, ConstraintProgram,
    ControlResult, ControlSnapshot, GeometryRef, JoystickSample, Keypoint, Matrix3, Message,
    MessageMeta, Number, Object, ObjectRelevance, ObjectWeight, ObjectWeights, Parameter, Payload, Pose,
    ProgramExecutionBinding, ProgramRef, ProgramValidationReceipt, Quaternion,
    Record, RobotState, SceneGeometry, SceneObservation, SemanticContext, SessionConfig,
    StampedTransform, TaskContext, TaskRef, TimePoint, Unit, Vector2, Vector3,
)
from .validation import loads_message, parse_message, parse_record, to_dict
from .host_constraints import (
    ConstraintMargin, HostConstraintChecker, HostConstraintResult, MeasuredStateView,
)

__version__ = "0.2.0"

__all__ = [
    "AssetRef", "Axis", "CommandAcceptanceRecord", "ConstraintEvaluation",
    "ConstraintMargin", "ConstraintProgram", "ControlResult", "ControlSnapshot", "GeometryRef",
    "HostConstraintChecker", "HostConstraintResult", "JoystickSample", "Keypoint", "Matrix3",
    "MeasuredStateView", "Message", "MessageMeta", "NUMERIC_OFFLINE_V1", "Number",
    "Object", "ObjectRelevance", "ObjectWeight", "ObjectWeights", "Parameter", "Payload", "Pose",
    "ProgramExecutionBinding", "ProgramRef", "ProgramValidationReceipt",
    "Quaternion", "Record", "RobotState", "SceneGeometry", "SceneObservation", "SemanticContext",
    "SessionConfig", "StampedTransform", "TaskContext", "TaskRef", "TimePoint", "Unit",
    "ValidationError", "ValidationPolicy", "Vector2", "Vector3", "loads_message",
    "parse_message", "parse_record", "to_dict",
]
