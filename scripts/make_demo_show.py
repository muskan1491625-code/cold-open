"""Generate an invented demo show ("The Lamp-Keeper's Daughter") in the pack's file format.

Why: README screenshots, the public demo and "run it on a new show" checks must
never use the client's scripts. Every line here is invented; run
`scripts/check_leaks.py` on the output before committing anything made from it.

Usage: uv run python scripts/make_demo_show.py OUT_DIR [--episodes 14] [--seed 7]
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

CAST = {
    "MARA": ["hopeful", "wary", "defiant", "broken", "quiet", "furious", "pleading", "tender"],
    "CORIN": ["calm", "dangerous", "dry", "earnest", "cold", "protective", "low"],
    "SELENE": ["shrill", "mocking", "cruel", "panicked", "sweet", "venomous"],
    "MAYOR BRISK": ["pompous", "cold", "nervous", "triumphant"],
    "WIDOW HALLORAN": ["venomous", "silken", "cold", "furious"],
    "TOBIN THE FERRYMAN": ["gruff", "kind", "urgent", "afraid"],
}
LINES = {
    "MARA": [
        "I kept that light burning for eleven winters.", "You will not take the lamp from me.",
        "Why does the sea give back only what it wants?", "I never stole from anyone in my life!",
        "If I say yes, the harbour keeps its tongue.", "Tell me the truth, or tell me nothing.",
        "My father built this tower with his own hands.", "Is this mercy, or is it another trap?",
        "I am done being the quiet sister.", "Then let them come with their torches!",
    ],
    "CORIN": [
        "Step away from her.", "The tide remembers every lie told on this quay.",
        "Marry me at dawn, and no one will dare touch you.", "I was drowned once. It did not take.",
        "Say her name again and you will regret it.", "The ring smells of your rose oil, Selene.",
        "I came back for one reason, and she is standing here.", "Nobody leaves this hall until I say so.",
    ],
    "SELENE": [
        "A lamp-keeper's girl in my mother's lace? Never!", "Search her pockets. She is a thief!",
        "You think a scarred stranger can save you?", "He is lying. Everyone knows he is lying!",
        "Smile, sister. It is your last night of freedom.", "Mother will see you hang for this.",
    ],
    "MAYOR BRISK": [
        "Order! I will have order in my hall!", "The council has already decided her fate.",
        "Lord Ashvale? That is impossible.", "Take the girl to the cells until morning.",
    ],
    "WIDOW HALLORAN": [
        "Drink deep, little lamp-keeper.", "Expose me and her father hangs beside me.",
        "Every wedding needs a widow. Why not two?", "You were always too soft for this family.",
    ],
    "TOBIN THE FERRYMAN": [
        "The boat leaves at the turn of the tide. Be on it.", "I saw who slipped the ring into your shawl.",
        "They are coming up the cliff path with lanterns!", "Your father paid me to watch over you.",
    ],
}
CAMERA = [
    "Wide shot of the storm-lashed harbour, lanterns swinging on the quay.", "Close-up on {a}'s hands gripping the rail.",
    "Extreme close-up on {a}'s eyes, wet with sea spray.", "Slow push-in on {a} as the lamp flickers behind them.",
    "Over-the-shoulder shot from {b} toward {a}.", "Low angle on {a} framed against the lighthouse beam.",
    "Medium two-shot of {a} and {b}, the space between them charged.", "Tight shot on a silver ring glinting in candlelight.",
    "High wide shot of the crowded hall turning toward the door.", "Close-up on {b}'s jaw tightening.",
]
ACTIONS = [
    "{a} grabs {b} by the wrist.", "{a} slaps the cup from {b}'s hand and it shatters.", "{a} kneels on the slick quay boards.",
    "{a} throws open the shutters; the storm floods in.", "{a} draws a short blade from a boot.",
    "{a} steps between {b} and the crowd.", "{a} tears the lace veil and lets it fall.",
]
SFX = ["A string sting swells to a peak.", "Thunder cracks over the harbour.", "The crowd gasps.",
       "A heartbeat pulse rises toward the cliffhanger.", "Glass shatters on stone."]
LOGLINES = [
    "Mara is accused of theft at her sister's betrothal feast until a hooded harbourmaster speaks for her.",
    "The harbourmaster is revealed as Lord Corin Ashvale, thought drowned, and offers Mara a bargain.",
    "On the wedding morning a poisoned cup forces Corin to choose between justice and Mara's family.",
    "Selene spreads a rumour that Mara lit a false light to wreck a ship.",
    "Tobin the ferryman reveals what he saw on the night of the feast.",
    "The council votes to take the lighthouse from Mara.",
    "Corin's past returns on a ship flying a black pennant.",
    "Mara discovers her father's logbook hidden in the lamp room.",
    "Widow Halloran offers Mara a deal that would save her father.",
    "A storm traps the whole family in the lighthouse overnight.",
    "Selene's betrothed confesses that he planted the ring.",
    "Corin is arrested for a murder from before his drowning.",
    "Mara must light the lamp to guide in the ship carrying Corin's accusers.",
    "The truth about the night Corin drowned comes out on the quay.",
]


def tc(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def episode(n: int, rng: random.Random) -> str:
    dur = rng.choice([72, 84, 96, 104, 118])
    cast = list(CAST)
    out = [f"EPISODE NUMBER: Episode {n}", f"EPISODE LOGLINE: {LOGLINES[(n - 1) % len(LOGLINES)]}",
           f"CONTINUITY NOTES: Episode {n} of the demo show. Mara wears a grey wool shawl. "
           f"The hall is called {'Marrowind' if n < 6 else 'Marrowynd' if n < 10 else 'Marrowinde'} Hall here.",
           "", "--- SCRIPT START ---", ""]
    split = dur // 2 if dur > 90 else dur
    scenes = [(1, 0, split), (2, split, dur)] if split < dur else [(1, 0, dur)]
    for num, s0, s1 in scenes:
        heading = rng.choice(["INT. MARROWIND HALL – NIGHT", "EXT. HARBOUR QUAY – NIGHT", "INT. LAMP ROOM – DAWN", "INT. HARBOUR CHAPEL – DAY"])
        out += [f"SCENE {num:02d}", f"Scene heading: {heading}", f"Scene start: {tc(s0)}", f"Scene end: {tc(s1)}", "", "Actions and dialogue:", ""]
        t = float(s0)
        while t < s1 - 0.5:
            shot = min(s1, t + rng.choice([3.0, 4.0, 5.0, 6.0]))
            a, b = rng.sample(["Mara", "Corin", "Selene", "the Mayor", "the widow", "Tobin"], 2)
            out += ["**CAMERA:**", f"Start: ({tc(t)}) | End: ({tc(shot)})", rng.choice(CAMERA).format(a=a, b=b), ""]
            if rng.random() < 0.18:
                out += ["**ACTION:**", f"Start: ({tc(t + 0.2)}) | End: ({tc(shot)})", rng.choice(ACTIONS).format(a=a, b=b), ""]
            if shot - t >= 2.5 and rng.random() < 0.85:
                who = rng.choice(cast)
                line = rng.choice(LINES[who])
                ln = min(shot - t - 0.6, max(1.2, len(line.split()) * 0.38))
                tags = ", ".join(t2.capitalize() for t2 in rng.sample(CAST[who], 2))
                tech = " (O.S.)" if rng.random() < 0.06 else ""
                out += [f"**{who}{tech} ({tags}):**", f"Start: ({tc(t + 0.4)}) | End: ({tc(t + 0.4 + ln)})", f'"{line}"', ""]
            if rng.random() < 0.08:
                out += ["**SOUND FX:**", f"Start: ({tc(max(t, shot - 1.5))}) | End: ({tc(shot)})", rng.choice(SFX), ""]
            t = shot
    out += ["--- SCRIPT END ---", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--episodes", type=int, default=14)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    for n in range(1, a.episodes + 1):
        (a.out / f"episode-{n:03d}-Episode {8000000 + n}.txt").write_text(episode(n, rng))
    print(f"wrote {a.episodes} invented episodes to {a.out}")


if __name__ == "__main__":
    main()
