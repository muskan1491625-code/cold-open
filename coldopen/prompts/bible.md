You are the story editor for a vertical microdrama. Build a continuity bible from the episode summaries and lists below. Use only what the material states; if unsure, say "unknown".

SHOW TITLE: {{title}}
EPISODE SUMMARIES (number, logline, continuity notes):
{{episodes}}
SPEAKER LABELS (label, first episode, last episode, line count):
{{labels}}
POSSIBLE SPELLING CONFLICTS FOUND BY CODE (decide which are the same entity):
{{conflicts}}

Return ONLY JSON with keys:
premise (2 sentences),
characters: [{canonical, aliases:[{label, first_ep, last_ep}], role, visual_descriptor}]
  - visual_descriptor = how a stranger would describe them on screen in ≤ 8 words, no names.
  - a label can map to different characters in different episode ranges; split spans accordingly.
arcs: [{name, first_ep, last_ep, summary}]  (4-8 arcs)
key_reveals: [{id:"R01", description, episode}]  (the 6-12 biggest reveals a promo could spoil)
continuity_conflicts: [{entity, variants:[{spelling, episodes:[...]}], note}]  (only true conflicts; different entities are NOT conflicts)
