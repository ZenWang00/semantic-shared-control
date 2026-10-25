# Scene, artifact and live/control test inputs

`bottle_cup_illustrative.json` preserves the complete JSON catalog from section 8
of `docs/wiki/Data-Model-and-Message-Contracts.md` without altering its values.
The complete worked chain is preserved, including live/control messages, nested
constraint evaluations and separate command-acceptance records. The `ssc-core`
0.2.0 tests parse these records alongside the scene, configuration and artifact
records introduced in Issue #4. Its accepted receipts describe the documentation's
**illustrative** state, not host-issued validation or permission to execute a
program. Parsing its robot state or acceptance log does not verify measured
feedback or actual command application.

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

The tests demonstrate the structural portions of S01–S08 and selected local
consistency and numeric-policy boundaries. Live/control cases exercise optional
velocity absence, explicit nullable fields, strict move/hold branches, weight
normalization tolerance and numeric rejection. They do not establish reference
resolution, candidate/program coverage, penalty-formula consistency, freshness,
measurement provenance, command authority or any runtime hold/stop behavior.
Optimizer, process-isolation, complete-loop and robot acceptance scenarios remain
separate work.
