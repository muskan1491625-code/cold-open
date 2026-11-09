"""Parser: fixture show (public) and the real pack (private, marked `pack`)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from coldopen.parse import ParseError, camera_boundaries, load_show, parse_episode_text, show_stats, split_header
from coldopen.timecode import fmt_smpte, fmt_tc, parse_tc

ROOT = Path(__file__).resolve().parent.parent
MINI = ROOT / "tests" / "fixtures" / "mini_show"


# -- timecodes ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,secs", [("00:33.80", 33.8), ("02:18.00", 138.0), ("01:02:03.50", 3723.5), ("03:20", 200.0), ("00:62.00", 62.0)]
)
def test_parse_tc(text, secs):
    assert parse_tc(text) == pytest.approx(secs)


def test_fmt_roundtrip_and_smpte():
    assert fmt_tc(93.456) == "01:33.46"
    assert fmt_smpte(61.5, 30) == "00:01:01:15"


def test_bad_tc():
    with pytest.raises(ValueError):
        parse_tc("1.2.3")


# -- headers ------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,speaker,tech,emo",
    [
        ("CAMERA", "CAMERA", [], []),
        ("SELENE (Shrill, Accusing)", "SELENE", [], ["shrill", "accusing"]),
        ("THE HOODED MAN (O.S.) (Calm, Commanding)", "THE HOODED MAN", ["O.S."], ["calm", "commanding"]),
        ("X (O.S. / Quiet, Dangerous)", "X", ["O.S."], ["quiet", "dangerous"]),
        ("X (Off-screen) (Dismissive)", "X", ["Off-screen"], ["dismissive"]),
        ("X (V.O.) (CONT'D) (Calm)", "X", ["V.O.", "CONT'D"], ["calm"]),
        ("X (O.S. / Continues)", "X", ["O.S.", "CONT'D"], []),
    ],
)
def test_split_header(label, speaker, tech, emo):
    assert split_header(label) == (speaker, tech, emo)


# -- fixture show ---------------------------------------------------------------


@pytest.fixture(scope="module")
def mini():
    return load_show(MINI)


def test_fixture_counts(mini):
    st = show_stats(mini)
    assert st["episodes"] == 3
    assert st["blocks"] == st["camera"] + st["dialogue"] + st["action"] + st["sfx"]
    assert st["end_before_start"] == 0


def test_ids_are_time_ordered_with_kind_tiebreak(mini):
    for ep in mini.episodes:
        keys = [(a.start, {"camera": 0, "action": 1, "dialogue": 2, "sfx": 3}[a.kind]) for a in ep.atoms]
        assert keys == sorted(keys)
        assert ep.atoms[0].id == f"E{ep.number:02d}.001"
        assert len({a.id for a in ep.atoms}) == len(ep.atoms)


def test_uppercase_variant_parses(mini):
    ep2 = mini.episodes[1]
    assert ep2.scenes[0]["heading"].startswith("INT.")
    assert ep2.duration == pytest.approx(62.0)
    assert any(a.kind == "sfx" for a in ep2.atoms)


def test_multiline_body_and_scenes(mini):
    ep3 = mini.episodes[2]
    assert [s["number"] for s in ep3.scenes] == [1, 2]
    act = next(a for a in ep3.atoms if a.kind == "action")
    assert "\n" not in act.text and act.text.endswith("smiles.")
    assert {a.scene for a in ep3.atoms} == {1, 2}


def test_dialogue_fields(mini):
    dl = [a for a in mini.episodes[0].atoms if a.kind == "dialogue"]
    assert all(a.speaker_label for a in dl)
    camera = [a for a in mini.episodes[0].atoms if a.kind == "camera"]
    assert all(a.speaker_label is None for a in camera)
    hooded = [a for a in dl if a.speaker_label == "THE HOODED HARBOURMASTER"]
    assert hooded[0].tech_tags == ["O.S."] and hooded[1].emotion_tags == ["quiet", "dangerous"]


def test_camera_boundaries_tile(mini):
    b = camera_boundaries(mini.episodes[0])
    assert b[0] == 0.0 and b[-1] == mini.episodes[0].duration
    assert 4.0 in b and 8.5 in b


def test_zip_input(tmp_path, mini):
    z = tmp_path / "show.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in MINI.glob("*.txt"):
            zf.write(f, f"Mini Show/{f.name}")
    sh = load_show(z)
    assert sh.title == "Mini Show"
    assert show_stats(sh) == show_stats(mini)


def test_max_episodes(mini):
    assert len(load_show(MINI, max_episodes=2).episodes) == 2


# -- loud failures ----------------------------------------------------------------

GOOD_HEAD = "EPISODE NUMBER: Episode 4\nEPISODE LOGLINE: x\nCONTINUITY NOTES: y\n\n--- SCRIPT START ---\n\nSCENE 01\nScene heading: INT. A – DAY\nScene start: 00:00.00\nScene end: 00:10.00\n\nActions and dialogue:\n\n"


def test_missing_timing_line_names_file_and_line():
    text = GOOD_HEAD + "**CAMERA:**\nWide shot.\n\n--- SCRIPT END ---\n"
    with pytest.raises(ParseError) as e:
        parse_episode_text(text, "ep4.txt")
    assert e.value.file == "ep4.txt" and e.value.line == 15 and "Start:" in e.value.expected


def test_end_before_start_fails():
    text = GOOD_HEAD + "**CAMERA:**\nStart: (00:05.00) | End: (00:04.00)\nShot.\n\n--- SCRIPT END ---\n"
    with pytest.raises(ParseError, match="End >= Start"):
        parse_episode_text(text, "ep4.txt")


def test_missing_script_end_fails():
    text = GOOD_HEAD + "**CAMERA:**\nStart: (00:00.00) | End: (00:04.00)\nShot.\n"
    with pytest.raises(ParseError, match="SCRIPT END"):
        parse_episode_text(text, "ep4.txt")


def test_stray_line_fails():
    text = GOOD_HEAD + "this line belongs nowhere\n--- SCRIPT END ---\n"
    with pytest.raises(ParseError, match="block header"):
        parse_episode_text(text, "ep4.txt")


# -- real pack (private) ----------------------------------------------------------


@pytest.mark.pack
def test_real_pack_counts():
    st = show_stats(load_show(ROOT / "data" / "pack"))
    assert st["episodes"] == 50
    assert st["blocks"] == 3727
    assert (st["camera"], st["dialogue"], st["action"], st["sfx"]) == (1754, 1487, 372, 114)
    # Spec says 621; it counted O.S./V.O./Off-screen as emotions. See BUILD_LOG 2026-11-08.
    assert st["emotion_tags"] == 615
    assert st["end_before_start"] == 0
    assert st["minutes"] == pytest.approx(102.7, abs=0.05)
    assert st["episodes_without_dialogue_first_3s"] == 33
