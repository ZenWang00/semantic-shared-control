"""Standard-library contracts and reference resolution for shared control.

Parsing validates local invariants; registries enforce immutable identities and
typed-record compatibility. Neither authorizes execution, activation or motion.
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
from .registry import (
    RegistryError, ResolvedBundle, ResolvedProgram, ResolvedSnapshot, ResolvedWeights,
    SessionRegistry,
)

__version__ = "0.3.0"

__all__ = [
    "AssetRef", "Axis", "CommandAcceptanceRecord", "ConstraintEvaluation",
    "ConstraintMargin", "ConstraintProgram", "ControlResult", "ControlSnapshot", "GeometryRef",
    "HostConstraintChecker", "HostConstraintResult", "JoystickSample", "Keypoint", "Matrix3",
    "MeasuredStateView", "Message", "MessageMeta", "NUMERIC_OFFLINE_V1", "Number",
    "Object", "ObjectRelevance", "ObjectWeight", "ObjectWeights", "Parameter", "Payload", "Pose",
    "ProgramExecutionBinding", "ProgramRef", "ProgramValidationReceipt",
    "Quaternion", "Record", "RegistryError", "ResolvedBundle", "ResolvedProgram",
    "ResolvedSnapshot", "ResolvedWeights", "RobotState", "SceneGeometry", "SceneObservation",
    "SemanticContext", "SessionConfig", "SessionRegistry", "StampedTransform", "TaskContext",
    "TaskRef", "TimePoint", "Unit",
    "ValidationError", "ValidationPolicy", "Vector2", "Vector3", "loads_message",
    "parse_message", "parse_record", "to_dict",
]
