"""Data-only VLM image proposals; no depth inference or motion generation.

The model proposes object IDs, relevance and normalized image coordinates.
Host-created IDs are hashes of their content and context, independent of order.
These points are deliberately not interchangeable with grounded CandidatePoint.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite

from .errors import ValidationError
from .policy import NUMERIC_CANDIDATES_V2, ValidationPolicy
from .validation import _ObjectPairs, _decode


@dataclass(frozen=True, slots=True)
class ImageCandidate:
    candidate_id: str
    object_id: str
    task_relevance: float
    point_yx: tuple[float, float]


@dataclass(frozen=True, slots=True)
class VLMProposal:
    schema_version: str
    task_id: str
    task_revision: int
    scene_id: str
    observation_id: str
    candidates: tuple[ImageCandidate, ...]


def parse_vlm_proposal(data: dict | str, *, known_object_ids: tuple[str, ...],
                       task_id: str, task_revision: int, scene_id: str,
                       observation_id: str,
                       policy: ValidationPolicy = NUMERIC_CANDIDATES_V2) -> VLMProposal:
    """Parse a strict response with schema_version and candidates only.

    Each candidate is {object_id, task_relevance, point_yx}. Image coordinates
    are normalized [y,x] in [0,1000]; task/scene provenance is supplied by the
    host that requested the response. A grounding adapter must subsequently
    produce metric TCP targets and may preserve these candidate IDs.
    """
    def fail(path, code, message):
        raise ValidationError(path, code, message)

    if type(policy) is not ValidationPolicy or policy != NUMERIC_CANDIDATES_V2:
        fail("$", "policy", "requires numeric-candidates-v2")
    if type(known_object_ids) is not tuple or not known_object_ids or any(
            type(item) is not str or not item for item in known_object_ids) or len(set(known_object_ids)) != len(known_object_ids):
        fail("$.known_object_ids", "type", "expected nonempty unique object IDs")
    if any(type(item) is not str or not item for item in (task_id, scene_id, observation_id)):
        fail("$", "type", "host context IDs must be nonempty strings")
    if type(task_revision) is not int or not 1 <= task_revision <= 9007199254740991:
        fail("$.task_revision", "range", "expected a positive safe revision integer")
    if type(data) is str:
        try:
            data = _decode(json.loads(data, object_pairs_hook=_ObjectPairs), "$")
        except (ValueError, RecursionError) as exc:
            if isinstance(exc, ValidationError):
                raise
            fail("$", "json", "invalid JSON response")
    if type(data) is not dict or set(data) != {"schema_version", "candidates"}:
        fail("$", "shape", "expected schema_version and candidates only")
    if type(data["schema_version"]) is not str or data["schema_version"] != "2.0":
        fail("$.schema_version", "version", "expected schema 2.0")
    points = data["candidates"]
    if type(points) is not list or not 1 <= len(points) <= policy.max_candidates:
        fail("$.candidates", "dimension", "expected 1 to 16 image candidates")
    result, identities, relevance_by_object = [], set(), {}
    for index, point in enumerate(points):
        path = f"$.candidates[{index}]"
        if type(point) is not dict or set(point) != {"object_id", "task_relevance", "point_yx"}:
            fail(path, "shape", "expected object_id, task_relevance and point_yx only")
        object_id = point["object_id"]
        if type(object_id) is not str or object_id not in known_object_ids:
            fail(path + ".object_id", "reference", "unknown scene object")
        relevance = point["task_relevance"]
        coordinates = point["point_yx"]
        if type(coordinates) is not list or len(coordinates) != 2:
            fail(path + ".point_yx", "dimension", "expected normalized [y,x]")
        for name, values, high in (("task_relevance", [relevance], 1), ("point_yx", coordinates, 1000)):
            for value in values:
                try:
                    valid = type(value) in (float, int) and isfinite(value) and 0 <= value <= high
                except OverflowError:
                    valid = False
                if not valid:
                    fail(path + "." + name, "range", f"expected finite number in [0,{high}]")
        relevance = float(relevance)
        if object_id in relevance_by_object and relevance_by_object[object_id] != relevance:
            fail(path + ".task_relevance", "value", "one object must have one task relevance")
        relevance_by_object[object_id] = relevance
        normalized = tuple(0.0 if value == 0 else float(value) for value in coordinates)
        identity = (object_id, normalized)
        if identity in identities:
            fail(path, "duplicate", "duplicate image candidate for object")
        identities.add(identity)
        canonical = json.dumps([task_id, task_revision, scene_id, observation_id, object_id, normalized],
                               ensure_ascii=True, separators=(",", ":"))
        candidate_id = "candidate-" + sha256(canonical.encode("utf-8")).hexdigest()
        result.append(ImageCandidate(candidate_id, object_id, relevance, normalized))
    return VLMProposal("2.0", task_id, task_revision, scene_id, observation_id,
                       tuple(sorted(result, key=lambda item: item.candidate_id)))
