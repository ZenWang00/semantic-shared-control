"""Data-only candidate contracts, deterministic intent and bounded control."""
from .errors import ValidationError
from .policy import NUMERIC_CANDIDATES_V2, ValidationPolicy
from .records import (
    AssetRef, Axis, CandidateEvaluation, CandidatePoint, CandidateScore,
    CandidateSelection, CandidateSet, CommandAcceptanceRecord, ControlResult,
    ControlSnapshot, GeometryRef, JoystickSample, Keypoint, Matrix3, Message,
    MessageMeta, Number, Object, ObjectRelevance, Payload, Pose, Quaternion,
    Record, RobotState, SceneGeometry, SceneObservation, SemanticContext,
    SessionConfig, StampedTransform, TaskContext, TaskRef, TimePoint, Unit,
    Vector2, Vector3,
)
from .validation import loads_message, parse_message, parse_record, to_dict
from .host_constraints import (
    ConstraintMargin, HostConstraintChecker, HostConstraintResult, MeasuredStateView,
)
from .registry import (
    RegistryError, ResolvedBundle, ResolvedSelection, ResolvedSnapshot, SessionRegistry,
)
from .fusion import IntentConfig, IntentOutcome, estimate_intent, get_intent_config
from .candidates import ImageCandidate, VLMProposal, parse_vlm_proposal
from .optimization import (
    AppliedCommandReference, HostCheckerSpec, OptimizationInputError, OptimizationOutcome,
    OptimizationRequest, PreparationOutcome, SolverConfig, SupervisedOptimizer,
    basic_step_margins, build_request, get_solver_config,
)

__version__ = "0.11.0"

__all__ = [
    "AppliedCommandReference", "AssetRef", "Axis", "CandidateEvaluation", "CandidatePoint",
    "CandidateScore", "CandidateSelection", "CandidateSet", "CommandAcceptanceRecord",
    "ConstraintMargin", "ControlResult", "ControlSnapshot", "GeometryRef", "HostCheckerSpec",
    "HostConstraintChecker", "HostConstraintResult", "ImageCandidate", "IntentConfig",
    "IntentOutcome", "JoystickSample", "Keypoint", "Matrix3", "MeasuredStateView", "Message",
    "MessageMeta", "NUMERIC_CANDIDATES_V2", "Number", "Object", "ObjectRelevance",
    "OptimizationInputError", "OptimizationOutcome", "OptimizationRequest", "Payload", "Pose",
    "PreparationOutcome", "Quaternion", "Record", "RegistryError", "ResolvedBundle",
    "ResolvedSelection", "ResolvedSnapshot", "RobotState", "SceneGeometry", "SceneObservation",
    "SemanticContext", "SessionConfig", "SessionRegistry", "SolverConfig", "StampedTransform",
    "SupervisedOptimizer", "TaskContext", "TaskRef", "TimePoint", "Unit", "VLMProposal",
    "ValidationError", "ValidationPolicy", "Vector2", "Vector3", "basic_step_margins",
    "build_request", "estimate_intent", "get_intent_config", "get_solver_config", "loads_message",
    "parse_message", "parse_record", "parse_vlm_proposal", "to_dict",
]
