# Candidate Point Contract

Schema 2.0 accepts data-only candidates. No source text, executable expression, entrypoint, execution binding or generated-program receipt participates in this contract.

## Image proposals versus control targets

A VLM proposal associates a known object with normalized image coordinates `[y,x]`, each in `[0,1000]`, and task relevance in `[0,1]`. Relevance is relative task support, not a calibrated probability. Host code assigns deterministic identities; reordering the same proposal data must not change their identities. Parsing validates data only and does not establish that a proposed point is graspable.

A grounded `CandidatePoint` contains `candidate_id`, `object_id`, and metre-valued `position_xyz`. Its enclosing `CandidateSet` pins the task revision, scene, feature set, observation and control frame. The position is a desired TCP position including the required gripper offset; a visible surface location is not automatically valid. Multiple candidates may belong to one object. A set contains 1–16 candidates with unique IDs.

The current offline pipeline supplies grounded synthetic targets directly. It does not invent a depth value or treat normalized image coordinates as robot coordinates. Live RGB-D backprojection and calibration are deferred.

## Selection and identity

The parser accepts this fixed-response shape (task and scene IDs come from the host's request context):

```json
{"schema_version":"2.0","candidates":[
  {"object_id":"cup","task_relevance":0.9,"point_yx":[420,610]},
  {"object_id":"cup","task_relevance":0.9,"point_yx":[440,640]}
]}
```

The offline mock instead supplies grounded points such as `{"candidate_id":"cup-left","object_id":"cup","position_xyz":[0.41,0.0,0.31]}` inside a context-bound CandidateSet. These are separate examples, not a claimed conversion between those pixels and that XYZ. From TCP `(0.40,0,0.30)`, a rightward joystick input can select this target and receive a positive Z component from its fixed distance cost. The joystick itself never provides a Z coordinate.

Each candidate inherits its object's task relevance and is scored against its own XY direction from current TCP. Use the current and historical unit directions to compute `C=(1+dot(u_hat,d))/2`, `H=(1+dot(h,d))/2`, and `score=0.25+relevance+2*C+0.5*H`. An absent/zero target projection gives neutral direction support. History is the bounded mean of enabled nonzero unit inputs.

Initially choose the largest score, then shortest full 3D distance, then lexicographically smallest stable ID. Retain the previous candidate unless a challenger exceeds its score by more than 0.1. Scores are per-candidate diagnostics; they are not summed into an attraction to a midpoint. Selection references the exact snapshot and candidate set. Previous selection is explicit, compatible and replayable rather than hidden estimator state.

Release, invalidation and context/epoch changes clear selection. Neutral or disabled input cannot produce assisted motion, even when the previous selection remains in the historical archive. Data identity and validation never grant motion permission.
