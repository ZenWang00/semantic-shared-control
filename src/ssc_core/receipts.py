"""Host-owned program acceptance. Registry compatibility alone is not trust.

Construct the authority from trusted host reviews/settings, never generator
payloads. It is session-local, single-threaded and initialization-only. Persisted
reports are replay evidence; a new authority must validate again after restart.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import uuid4

from . import records as r
from .errors import ValidationError
from .penalties import HostNormalization
from .policy import NUMERIC_OFFLINE_V1
from .program_validation import NumericalValidation, probe_program
from .registry import RegistryError, SessionRegistry, _canonical, _definition
from .validation import parse_record, to_dict


def _parse(cls, value):
    return parse_record(cls, to_dict(value) if isinstance(value, r.Record) else value,
                        policy=NUMERIC_OFFLINE_V1)


def _digest(value):
    encoded = json.dumps(_canonical(value), ensure_ascii=True, separators=(",", ":"))
    return sha256(encoded.encode("ascii")).hexdigest()


def program_content_digest(program: r.ConstraintProgram | dict) -> str:
    """ssc-review-v1 typed-content digest, including exact source and all fields."""
    return _digest(to_dict(_parse(r.ConstraintProgram, program)))


def feature_definition_digest(geometry: r.SceneGeometry | dict) -> str:
    """Order-independent feature membership/ownership/directedness and frame.

    Coordinate values/revisions and display labels are deliberately excluded;
    scene and feature-set identity are separately pinned by the host review.
    """
    geometry = _parse(r.SceneGeometry, geometry)
    return _digest((geometry.frame_id, _definition(geometry)))


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class UnitReview:
    """Trusted dimensional/feature review of one complete immutable artifact.

    Creating this value records a host assertion, not an automated unit proof.
    The reviewer must inspect the formula and bound feature meanings. The
    generator cannot supply reviews to the host authority's configuration.
    """
    review_id: str
    program_digest: str
    scene_id: str
    feature_set_id: str
    feature_digest: str
    output_unit: r.Unit
    parameter_units: tuple[r.Unit, ...]
    reviewer: str
    rationale: str

    def __post_init__(self):
        for name in ("review_id", "scene_id", "feature_set_id", "reviewer", "rationale"):
            _text(getattr(self, name), name)
        for name in ("program_digest", "feature_digest"):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if type(self.parameter_units) is not tuple:
            raise TypeError("parameter_units must be immutable")
        if any(type(unit) is not str or unit not in ("m", "rad", "dimensionless")
               for unit in (self.output_unit, *self.parameter_units)):
            raise ValueError("unsupported review unit")


@dataclass(frozen=True, slots=True)
class HostBindingPolicy:
    config_id: str
    program_id: str
    normalization: HostNormalization

    def __post_init__(self):
        _text(self.config_id, "config_id")
        _text(self.program_id, "program_id")
        if type(self.normalization) is not HostNormalization:
            raise TypeError("expected trusted HostNormalization")


class AcceptanceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class ProgramValidationReport:
    receipt: r.ProgramValidationReceipt
    program: r.ConstraintProgram
    geometry: r.SceneGeometry
    config_id: str
    unit_review: UnitReview | None
    normalization: HostNormalization | None
    numerical: NumericalValidation | None


class HostProgramValidator:
    """Issue receipts only after trusted review, resolution and bounded probes.

    All access to this authority and its registry must be serialized by the host.
    This is an in-process trust boundary for generated data, not cryptographic
    attestation or protection against arbitrary code in the host process.
    """

    def __init__(self, registry: SessionRegistry, *, unit_reviews: tuple[UnitReview, ...],
                 binding_policies: tuple[HostBindingPolicy, ...]):
        if type(registry) is not SessionRegistry:
            raise TypeError("expected SessionRegistry")
        self._registry = registry
        self._reviews = {}
        self._settings = {}
        self._issued: dict[str, ProgramValidationReport] = {}
        for review in unit_reviews:
            if type(review) is not UnitReview:
                raise TypeError("expected trusted UnitReview values")
            if review.review_id in self._reviews:
                raise ValueError("duplicate review identity")
            self._reviews[review.review_id] = review
        for setting in binding_policies:
            if type(setting) is not HostBindingPolicy:
                raise TypeError("expected trusted HostBindingPolicy values")
            key = (setting.config_id, setting.program_id)
            if key in self._settings:
                raise ValueError("duplicate configuration/program normalization")
            self._settings[key] = setting.normalization

    def _get(self, cls, identity):
        return self._registry.get_record(cls, identity, session_id=self._registry.session_id)

    def _register(self, cls, value):
        return self._registry.register_record(cls, value, session_id=self._registry.session_id)

    def _policy(self, config_id):
        if self._registry.policy != NUMERIC_OFFLINE_V1:
            raise AcceptanceError("policy_mismatch", "host and worker must use exact numeric-offline-v1 settings")
        configuration = self._get(r.SessionConfig, config_id)
        if configuration.validation_policy_id != NUMERIC_OFFLINE_V1.policy_id:
            raise AcceptanceError("policy_mismatch", "configuration requests an unsupported policy")

    def _normalization(self, config_id, program):
        settings = self._settings.get((config_id, program.program_id))
        if settings is None:
            raise AcceptanceError("missing_normalization", "host configuration has no program settings")
        if settings.unit != program.output_unit:
            raise AcceptanceError("unit_mismatch", "normalization unit differs from program output unit")
        return settings

    def _review(self, program, geometry):
        digest = program_content_digest(program)
        definition = feature_definition_digest(geometry)
        for review in self._reviews.values():
            if (review.program_digest == digest and review.scene_id == geometry.scene_id
                    and review.feature_set_id == geometry.feature_set_id and review.feature_digest == definition):
                if (review.output_unit != program.output_unit
                        or review.parameter_units != tuple(p.unit for p in program.parameters)):
                    raise AcceptanceError("unit_mismatch", "artifact declarations differ from host unit review")
                return review
        raise AcceptanceError("missing_unit_review", "no host review covers this complete artifact and feature definition")

    def validate(self, program: r.ConstraintProgram | dict, *, geometry: r.SceneGeometry | dict,
                 config_id: str, motion_disabled: bool) -> ProgramValidationReport:
        # Invalid structure/ABI/unit enums cannot form a valid artifact/receipt.
        program = _parse(r.ConstraintProgram, program)
        geometry = _parse(r.SceneGeometry, geometry)
        diagnostics = []
        review = normalization = numerical = None
        try:
            self._policy(config_id)
            if motion_disabled is not True:
                raise AcceptanceError("motion_not_disabled", "validation is initialization-only")
            self._register(r.ConstraintProgram, program)
            self._register(r.SceneGeometry, geometry)
            resolved = self._registry.resolve_program(
                r.ProgramRef(program.program_id, program.source_sha256),
                geometry_ref=r.GeometryRef(geometry.geometry_id, geometry.revision), config_id=config_id)
            review = self._review(program, geometry)
            normalization = self._normalization(config_id, program)
            numerical = probe_program(resolved, motion_disabled=True)
            diagnostics.extend(numerical.diagnostics)
            if not numerical.accepted and not diagnostics:
                diagnostics.append("probe_failure: numerical validation did not accept")
        except (RegistryError, AcceptanceError, ValidationError) as exc:
            diagnostics.append(str(exc))
        receipt = _parse(r.ProgramValidationReceipt, {
            "receipt_id": "receipt-" + uuid4().hex,
            "program_id": program.program_id,
            "source_sha256": sha256(program.source_code.encode("utf-8")).hexdigest(),
            "function_api_version": program.function_api_version,
            "scene_id": geometry.scene_id, "feature_set_id": geometry.feature_set_id,
            "validation_geometry_id": geometry.geometry_id,
            "validation_geometry_revision": geometry.revision,
            "validator_policy_version": NUMERIC_OFFLINE_V1.policy_id,
            "status": "rejected" if diagnostics else "accepted",
            "diagnostics": diagnostics,
        })
        # Persist first, then confer local trust; collisions cannot overwrite.
        self._register(r.ProgramValidationReceipt, receipt)
        report = ProgramValidationReport(receipt, program, geometry, config_id, review, normalization, numerical)
        self._issued[receipt.receipt_id] = report
        return report

    def verify_receipt(self, receipt_id: str, *, program_ref: r.ProgramRef | dict,
                       geometry_ref: r.GeometryRef | dict, config_id: str) -> r.ProgramValidationReceipt:
        """Check local issuance plus current artifact/scene/features/policy.

        Geometry coordinate revisions may differ with identical definitions.
        This check does not waive static-scene reinitialization or runtime checks.
        """
        self._policy(config_id)
        report = self._issued.get(receipt_id)
        if report is None or report.receipt.status != "accepted":
            raise AcceptanceError("untrusted_receipt", "receipt was not accepted by this authority")
        receipt = self._get(r.ProgramValidationReceipt, receipt_id)
        if receipt != report.receipt:
            raise AcceptanceError("receipt_mismatch", "stored receipt differs from issued content")
        resolved = self._registry.resolve_program(program_ref, geometry_ref=geometry_ref, config_id=config_id)
        if program_content_digest(resolved.program) != program_content_digest(report.program):
            raise AcceptanceError("artifact_mismatch", "receipt covers a different complete artifact")
        geometry = resolved.geometry
        if (geometry.scene_id != receipt.scene_id or geometry.feature_set_id != receipt.feature_set_id
                or feature_definition_digest(geometry) != feature_definition_digest(report.geometry)):
            raise AcceptanceError("feature_mismatch", "receipt covers a different scene or feature definition")
        # Provenance remains resolvable at the original exact coordinate revision.
        self._registry.resolve_program(program_ref,
            geometry_ref=r.GeometryRef(receipt.validation_geometry_id, receipt.validation_geometry_revision),
            config_id=config_id)
        if self._review(resolved.program, geometry) != report.unit_review:
            raise AcceptanceError("review_mismatch", "host review differs from acceptance evidence")
        return receipt

    def create_binding(self, binding_id: str, *, receipt_id: str,
                       geometry_ref: r.GeometryRef | dict, config_id: str) -> r.ProgramExecutionBinding:
        report = self._issued.get(receipt_id)
        if report is None:
            raise AcceptanceError("untrusted_receipt", "unknown issuer receipt")
        program = report.program
        self.verify_receipt(receipt_id, program_ref=r.ProgramRef(program.program_id, program.source_sha256),
                            geometry_ref=geometry_ref, config_id=config_id)
        settings = self._normalization(config_id, program)
        binding = _parse(r.ProgramExecutionBinding, {
            "binding_id": binding_id, "program_id": program.program_id, "config_id": config_id,
            "normalization_scale": settings.scale, "weight": settings.weight,
            "validation_receipt_id": receipt_id,
        })
        return self._register(r.ProgramExecutionBinding, binding)

    def validate_binding(self, binding_id: str, *, geometry_ref: r.GeometryRef | dict,
                         config_id: str) -> r.ProgramExecutionBinding:
        binding = self._get(r.ProgramExecutionBinding, binding_id)
        if binding.config_id != config_id:
            raise AcceptanceError("configuration_mismatch", "binding belongs to another configuration")
        program = self._get(r.ConstraintProgram, binding.program_id)
        self.verify_receipt(binding.validation_receipt_id,
                            program_ref=r.ProgramRef(program.program_id, program.source_sha256),
                            geometry_ref=geometry_ref, config_id=config_id)
        settings = self._normalization(config_id, program)
        if _canonical((binding.normalization_scale, binding.weight)) != _canonical((settings.scale, settings.weight)):
            raise AcceptanceError("normalization_mismatch", "binding differs from immutable host settings")
        return binding
