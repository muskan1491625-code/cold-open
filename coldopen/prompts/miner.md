You find promo clips for a vertical microdrama. A promo is a 20-60 second clip (aim for 30-45) posted on TikTok, Instagram or YouTube to pull in NEW viewers who have never seen the show.

A great promo:
1. HOOK: something in the first 3 seconds that stops a scrolling thumb — a slap, a reveal, a shocking line, a humiliation, a threat.
2. CLIFFHANGER: it ends on an unresolved moment. Cut BEFORE the answer, the reaction, or the rescue.
3. STANDS ALONE: a stranger understands who is who and what is at stake from the clip itself.

Structures you may propose:
- straight: one continuous range of atoms.
- flash_forward: segment 1 is a 2-4 second peak moment taken from LATER (same or next episode), then the setup, then the cliffhanger. Its segment 1 may use the NEXT EPISODE PEAK ATOMS listed below.

Story engines (pick the one promise the promo makes):
humiliation_reversal, dangerous_protector, hidden_identity, sacrifice_ultimatum, framed_condemned, family_betrayal, forbidden_scandal, other.

Rules:
- Use ONLY atom IDs that appear below. Never write timestamps.
- hook_id must be an atom that starts within 3 seconds of the start of segment 1 (so segment 1 should begin at most a shot before the hook).
- cliffhanger_id must be the last spoken line or action of the last segment.
- Copy hook_text and cliffhanger_text exactly from those atoms.
- Segments are given as from_id/to_id ranges of atoms in the SAME episode.
- Propose 3-6 candidates. At least ONE must be a "quiet_power" moment: a quiet, cruel, or tender line that is NOT at a listed tension hotspot.
- Prefer moments that need no prior episodes. Avoid starting on establishing shots.

SHOW BIBLE (digest):
{{bible_digest}}
PREVIOUS EPISODE LOGLINE: {{prev_logline}}
NEXT EPISODE LOGLINE: {{next_logline}}
NEXT EPISODE PEAK ATOMS (only for a flash_forward segment 1):
{{next_peaks}}
TENSION HOTSPOTS (second, intensity, atoms):
{{hotspots}}
EPISODE {{episode}} ATOMS (id | start-end | kind | speaker/tags | text):
{{atoms}}

Return ONLY JSON: {"candidates": [{"structure", "segments":[{"from_id","to_id"}], "hook_id", "cliffhanger_id", "engine", "hook_text", "cliffhanger_text", "why", "miner_hook":1-5, "miner_cliff":1-5, "quiet_power": bool}]}
"why" is one sentence a marketing lead would find useful.
