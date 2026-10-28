# Candidate control fixtures

`bottle_cup_candidates_v2.json` is the active schema-2 synthetic catalog. It contains observation metadata, two objects, task relevance and already-grounded TCP candidate positions. Initial TCP is `(0.4, 0, 0.3)`, with right/left candidate targets `(0.41, 0, 0.3)` and `(0.39, 0, 0.3)`. It includes no generated source, binding or validation receipt. Runtime variants exercise multiple points on one object, higher/equal/lower targets, and candidate counts 1, 2 and 16.

These positions are declared mock inputs; the image metadata does not establish a tested pixel-to-3D conversion. The fixed VLM response/parser tests live in `tests/shared_control/test_candidates.py` and validate normalized image `[y,x]` data separately.

`control_run_v1.json` and its checksum are preserved historical experiment records. The current archive test verifies explicit rejection without modifying those bytes. Use their historical Git/package version to reproduce schema-1 behavior. Old standalone generated-program catalogs have been removed from the active fixture directory and remain in Git history.

Current acceptance evidence comes from the schema-2 tests, `docs/reference/verify_candidates.py`, and the consumer's installed-wheel Linux integration. Historical evidence is not counted as validation of the new candidate logic.
