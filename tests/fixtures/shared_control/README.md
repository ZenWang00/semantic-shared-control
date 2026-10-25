# Scene and artifact test inputs

`bottle_cup_illustrative.json` preserves the complete JSON catalog from section 8
of `docs/wiki/Data-Model-and-Message-Contracts.md` without altering its values.
The catalog includes future live/control messages so its original worked chain
remains intact. Issue #4 tests only its scene, configuration and artifact records;
it does not claim that every message in the catalog is implemented. Its accepted
receipts describe the documentation's **illustrative** state, not host-issued
validation or permission to execute a program.

`bottle_cup_offline_v1.json` is a separate **synthetic** structural fixture based
on `offline-reference-v1`, with fresh scene, geometry, feature, program, task and
message IDs. It uses `config-offline-v1`, the specified synthetic frames/model,
initial TCP `(0.4, 0, 0.3)`, approach points `(0.41, 0, 0.3)` and
`(0.39, 0, 0.3)`, relevance `0.5` each, zero distance tolerance and `0.01 m`
normalization. The initial joint coordinates are all zero and their synthetic
provenance is explicit. It provides no physical observations or real robot
calibration evidence.

The synthetic fixture deliberately contains **no validation receipts**. Its
execution bindings reference unissued receipt IDs to exercise structural parsing;
the fixture is not a resolved, validated, or activated runtime bundle. Hashes are
preserved for the exact example source bytes; these tests validate hash format
only. Trusted receipt issuance, hash verification, cross-artifact resolution and
runtime execution remain later stages.

The tests demonstrate only the scene/artifact portions of S01–S05 and selected
local consistency and numeric-policy boundaries. They do not establish the
remaining live-input, optimizer, process-isolation or robot acceptance scenarios.
