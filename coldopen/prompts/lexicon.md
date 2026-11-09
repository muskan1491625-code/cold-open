These are delivery notes from screenplay dialogue headers. For each, estimate:
- arousal: 0 (calm) to 1 (extremely heightened)
- valence: -1 (hostile/painful) to 1 (warm/joyful)
- is_emotion: false if it only describes technique or volume without emotion (e.g. "o.s.", "v.o.", "off-screen")
Return ONLY JSON: {"tags": {"<tag>": {"arousal": x, "valence": y, "is_emotion": bool}, ...}}
TAGS:
{{tags}}
