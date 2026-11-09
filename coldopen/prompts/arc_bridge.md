You combine moments from different episodes into one 20-60 second promo that compresses a mini-arc (for example: humiliation in one episode, rescue or reversal in a later one), cutting on an unresolved beat.
Use the candidate moments and arcs below. Each combo has 2-3 segments in playback order, may trim a moment using any atom IDs inside it, and must still open with a hook in its first 3 seconds and end on a cliffhanger.
ARCS: {{arcs}}
CANDIDATE MOMENTS (id, episode, atom range, engine, hook, cliffhanger, atom list):
{{moments}}
Rules:
- Name atoms by ID only (like E01.026). Never write timestamps or durations: code computes them from the IDs.
- Each segment is a from_id/to_id range of atoms in ONE episode; segments come from at least two episodes.
- hook_id must be an atom that starts within 3 seconds of the start of segment 1.
- cliffhanger_id must be the last spoken line or action of the last segment.
- engine is one of: humiliation_reversal, dangerous_protector, hidden_identity, sacrifice_ultimatum, framed_condemned,
  family_betrayal, forbidden_scandal, other.
- hook_text and cliffhanger_text are copied word for word from those atoms.

Return ONLY JSON, up to 8 candidates, exactly these fields:
{"candidates": [{"structure": "arc_bridge", "segments": [{"from_id": "E01.026", "to_id": "E01.031"}, {"from_id": "E03.047", "to_id": "E03.052"}], "hook_id": "...", "cliffhanger_id": "...", "engine": "...", "hook_text": "...", "cliffhanger_text": "...", "why": "one sentence", "miner_hook": 1-5, "miner_cliff": 1-5, "quiet_power": false}]}
