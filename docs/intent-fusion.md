# Deterministic candidate selection

`intent-candidates-v2` consumes one immutable resolved snapshot. It scores each grounded candidate using its object's relevance plus current and bounded historical joystick direction. The formula and switch rules are specified in [Candidate Point Contract](wiki/Candidate-Point-Contract.md).

The result is `CandidateSelection`, containing raw candidate scores/distances, selected candidate ID, source snapshot and candidate-set IDs, configuration ID and separate assistance strength. There are no object-weighted averages of candidate positions. Alpha-zero remains an active manual XY mode; neutral/disabled input returns disabled and cannot request motion.

Previous selection is an explicit snapshot reference checked against the same context and epoch. Runtime reset removes it on release/invalidation. Replaying the same pinned inputs reproduces the choice; array ordering does not break ties. Identical-direction alternatives are distinguished by relevance, 3D distance and stable ID, not by how many points an object owns.

The 0.1 switching margin and other coefficients are fixed engineering defaults for this offline profile. They are not calibrated intent probabilities or experimentally established optimal gains.
