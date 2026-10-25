import math

import pytest

from ssc_core import NUMERIC_OFFLINE_V1, SessionRegistry
from ssc_core.probes import IDENTITY, ORIGIN, _align_x, validation_probes


def resolved_fixture(catalog):
    registry = SessionRegistry("session-offline-v1", policy=NUMERIC_OFFLINE_V1)
    for message in catalog["messages"]:
        registry.register_message(message)
    program = catalog["constraint_programs"][0]
    registry.register_record("ConstraintProgram", program, session_id=registry.session_id)
    geometry = next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SceneGeometry")
    return registry.resolve_program(
        {"program_id": program["program_id"], "source_sha256": program["source_sha256"]},
        geometry_ref={"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]},
        config_id="config-offline-v1")


def test_named_grid_and_target_order(offline_catalog):
    resolved = resolved_fixture(offline_catalog)
    probes = validation_probes(resolved)
    assert len(probes) == 50
    assert probes[0].position == ORIGIN and probes[0].rotation == IDENTITY
    assert [p.name for p in probes[:7]] == [
        "grid/origin/identity", "grid/origin/x+", "grid/origin/x-", "grid/origin/y+",
        "grid/origin/y-", "grid/origin/z+", "grid/origin/z-",
    ]
    assert probes[7].position == pytest.approx((.41, 0., .3), abs=1e-12)
    assert probes[-1].position == resolved.keypoints[0]
    assert probes == validation_probes(resolved)


@pytest.mark.parametrize("direction", [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, -1),
                                       (0.6, 0.8, 0), (-1., 1e-10, 0)])
def test_shortest_alignment_is_a_proper_rotation(direction):
    rotation = _align_x(direction)
    assert [row[0] for row in rotation] == pytest.approx(direction, abs=1e-9)
    for i in range(3):
        for j in range(3):
            assert sum(rotation[k][i] * rotation[k][j] for k in range(3)) == pytest.approx(int(i == j), abs=1e-9)
    determinant = sum(rotation[0][i] * (rotation[1][(i+1)%3] * rotation[2][(i+2)%3]
                         - rotation[1][(i+2)%3] * rotation[2][(i+1)%3]) for i in range(3))
    assert math.isclose(determinant, 1., abs_tol=1e-9)
