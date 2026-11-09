"""Stage 1 detectors, speaker resolution, lexicon plumbing, and the EKG maths."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from coldopen.bible import label_table, proper_nouns, resolve_speaker, spelling_conflicts
from coldopen.config import Config
from coldopen.ekg import atom_score, build_ekg, gaussian_smooth, hotspots, normalise
from coldopen.lexicon import build_lexicon
from coldopen.llm.provider import LLMClient
from coldopen.models import Atom, Episode, LexiconEntry
from coldopen.parse import load_show
from tests.helpers import mini

ROOT = Path(__file__).resolve().parent.parent
SHOW, BIBLE, IX = mini()


def test_label_table_spans():
    rows = {r["label"]: r for r in label_table(SHOW)}
    assert rows["CORIN"]["first_ep"] == 2 and rows["CORIN"]["last_ep"] == 3
    assert rows["THE HOODED HARBOURMASTER"]["lines"] == 2


def test_fixture_spelling_drift_found():
    groups = spelling_conflicts(proper_nouns(SHOW))
    spellings = [{v["spelling"] for v in g["variants"]} for g in groups]
    assert {"Marrowind", "Marrowynd", "Marrowinde"} in spellings
    assert not any("Marlowe" in s for s in spellings)  # a different family, never merged


def test_conflicts_require_prefix_and_ratio():
    nouns = {"Hollowmere": {1}, "Hollowmeer": {5}, "Holloway": {2}, "Shallowmere": {3}}
    groups = spelling_conflicts(nouns)
    assert [sorted(v["spelling"] for v in g["variants"]) for g in groups] == [["Hollowmeer", "Hollowmere"]]


def test_episode_scoped_alias():
    assert resolve_speaker(BIBLE, "THE HOODED HARBOURMASTER", 1) == "Corin Ashvale"
    assert resolve_speaker(BIBLE, "THE HOODED HARBOURMASTER", 3) is None  # alias ends in E1
    assert resolve_speaker(BIBLE, "CORIN", 3) == "Corin Ashvale"
    hooded = [a for a in SHOW.episodes[0].atoms if a.speaker_label == "THE HOODED HARBOURMASTER"]
    assert {a.speaker for a in hooded} == {"Corin Ashvale"}


@pytest.mark.pack
def test_real_pack_house_conflict_detected():
    """Expected spellings live in git-ignored data/pack_expectations.yaml (they are show details)."""
    import yaml

    exp_file = ROOT / "data" / "pack_expectations.yaml"
    if not exp_file.exists():
        pytest.skip("data/pack_expectations.yaml not present")
    exp = yaml.safe_load(exp_file.read_text())["house_conflict"]
    groups = spelling_conflicts(proper_nouns(load_show(ROOT / "data" / "pack")))
    house = next(g for g in groups if any(v["spelling"] == exp["same"][0] for v in g["variants"]))
    names = {v["spelling"] for v in house["variants"]}
    assert set(exp["same"]) <= names
    assert exp["different"] not in names


# -- lexicon --------------------------------------------------------------------


def test_lexicon_covers_all_tags_and_clamps(tmp_path):
    from coldopen.llm.mockbrain import MockBrain

    cfg = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"}, rpm=1000)
    client = LLMClient(cfg, provider=MockBrain(), prices={})
    lex, stats = asyncio.run(build_lexicon(SHOW, client, cache_root=tmp_path))
    tags = {t for e in SHOW.episodes for a in e.atoms for t in a.emotion_tags}
    assert set(lex) == tags and stats["missing"] == 0
    assert all(0 <= e.arousal <= 1 and -1 <= e.valence <= 1 for e in lex.values())
    assert lex["furious"].arousal > lex["calm"].arousal


# -- EKG ------------------------------------------------------------------------

LEX = {"furious": LexiconEntry(arousal=0.9, valence=-0.8, is_emotion=True),
       "whispering": LexiconEntry(arousal=0.2, valence=0.0, is_emotion=False)}


def _atom(kind, text, tags=(), s=0.0, e=2.0):
    return Atom(id="E01.001", episode=1, scene=1, kind=kind, start=s, end=e, text=text, emotion_tags=list(tags))


def test_atom_scores_follow_spec():
    d = _atom("dialogue", '"Get out! Now! Go!"', ["furious"], 0, 2)  # 4 words over 2 s
    assert atom_score(d, LEX) == pytest.approx(0.9 + 0.25 * 2 + 0 + 0.05 * 2)
    q = _atom("dialogue", '"Why?"', ["whispering"], 0, 1)  # non-emotion tag -> default 0.3
    assert atom_score(q, LEX) == pytest.approx(0.3 + 0.1 + 0.05)
    assert atom_score(_atom("action", "He grabs her and draws a sword."), LEX) == pytest.approx(1.0)
    assert atom_score(_atom("action", "A fire crackles in the hearth."), LEX) == pytest.approx(0.5)  # known false positive
    assert atom_score(_atom("sfx", "A string sting swells."), LEX) == 0.6
    assert atom_score(_atom("camera", "Slow push-in on her face."), LEX) == 0.15
    assert atom_score(_atom("camera", "Wide shot of the hall."), LEX) == 0.0


def test_smoothing_keeps_constants_and_spreads_spikes():
    assert np.allclose(gaussian_smooth(np.ones(20)), 1.0)
    x = np.zeros(21)
    x[10] = 1.0
    y = gaussian_smooth(x)
    assert y.argmax() == 10 and y[10] < 1.0 and y[7] > 0


def test_normalise_uses_show_wide_percentiles():
    a, b = np.linspace(0, 1, 101), np.linspace(0, 2, 101)
    (na, nb), lo, hi = normalise([a, b])
    assert na.min() == 0 and nb.max() == 1 and 0 < lo < hi


def test_hotspots_threshold_and_gap():
    ep = Episode(number=1, logline="", continuity_notes="", duration=40, scenes=[], atoms=[])
    v = np.zeros(40)
    v[[5, 9, 20, 30]] = [0.9, 0.95, 0.5, 0.7]
    hs = hotspots(v, ep)
    assert [h["t"] for h in hs] == [9.0, 30.0]  # 5 is within 8 s of the stronger 9; 20 is below 0.6


def test_build_ekg_shapes():
    ekgs = build_ekg(SHOW, LEX)
    assert [len(e.values) for e in ekgs] == [66, 62, 64]
    assert all(0 <= v <= 1 for e in ekgs for v in e.values)
