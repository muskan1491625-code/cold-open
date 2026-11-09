"""A deterministic, heuristic stand-in for every prompt, used by `--provider mock`.

Why it exists: the whole pipeline (validators, repair loop, scoring, exports,
UI) must be testable and demoable without API keys or network. It reads the
*rendered prompt* the same way a model would and answers with plausible JSON.

What it is NOT: a judge of meaning. Every answer it gives is marked "[mock]"
and run reports carry a note. Its weaknesses are useful, though: it is exactly
the "loudness only" baseline the real LLM has to beat.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from coldopen.llm.provider import BaseProvider, Completion
from coldopen.timecode import parse_tc

ATOM_LINE = re.compile(r"^\s*(E\d+\.\d+) \| (\d+:\d+\.\d+)-(\d+:\d+\.\d+) \| (CAM|ACT|DLG|SFX) \| (.*)$")
ENGINE_KW = {
    "humiliation_reversal": r"slap|humiliat|laugh|mock|beneath|servant|shame|kneel",
    "dangerous_protector": r"protect|sword|guard|stranger|scar|release her|touch her",
    "hidden_identity": r"who are you|identity|disguise|secret|truly|revealed|real name",
    "framed_condemned": r"poison|trial|accus|thief|guilty|hang|gallows|cell|chain",
    "family_betrayal": r"sister|mother|father|family|brother|daughter",
    "forbidden_scandal": r"kiss|scandal|love|marry|wedding|bride",
    "sacrifice_ultimatum": r"sacrifice|choose|unless|or else|in exchange|bargain",
}
HIGH = r"furious|rage|scream|shout|desperate|panic|terrif|frantic|hyster|enrag|shock|horrif|venom|agon|explos|wild"
MID = r"angr|sharp|urgent|threat|accus|bitter|defian|anguish|broken|cold|harsh|stern|tense|fear|trembl|cruel|mock"
LOW = r"calm|soft|gentle|quiet|tender|warm|weary|tired|sooth|whisper|fond|amused|dry"
POS = r"warm|tender|lov|joy|hope|amus|teas|reliev|grate|fond|gentle|happy|bright|kind|proud"
NEG = r"furious|rage|cold|cruel|venom|bitter|fear|terrif|accus|broken|anguish|hostile|harsh|mock|threat|desper|panic"
TECHNIQUE = {"low", "loud", "whispering", "whisper", "muffled", "softly", "quietly", "aside", "beat", "pause"}


def _atoms(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = ATOM_LINE.match(line)
        if not m:
            continue
        aid, s, e, kind, rest = m.groups()
        parts = rest.split(" | ", 1)
        txt = parts[1] if kind == "DLG" and len(parts) == 2 else rest
        out.append({"id": aid, "start": parse_tc(s), "end": parse_tc(e), "kind": kind, "text": txt.strip()})
    return out


def _section(text: str, head: str, stop: str) -> str:
    i = text.find(head)
    if i < 0:
        return ""
    j = text.find(stop, i + len(head)) if stop else -1
    return text[i + len(head) : j if j >= 0 else None]


def _engine(texts: str) -> str:
    scores = {k: len(re.findall(v, texts, re.I)) for k, v in ENGINE_KW.items()}
    k = max(scores, key=lambda x: (scores[x], x))
    return k if scores[k] else "other"


def _cam_before(atoms: list[dict], hook: dict) -> dict:
    cams = [a for a in atoms if a["kind"] == "CAM" and hook["start"] - 2.6 <= a["start"] <= hook["start"]]
    return cams[-1] if cams else hook


def _cliff_for(dlg: list[dict], hook: dict, lo: float = 26, hi: float = 46) -> dict | None:
    pool = [d for d in dlg if lo <= d["end"] - hook["start"] <= hi and d["id"] != hook["id"]]
    if not pool:
        return None
    asks = [d for d in pool if "?" in d["text"] or "!" in d["text"] or d["text"].endswith('..."')]
    return (asks or pool)[len(asks or pool) // 2]


class MockBrain(BaseProvider):
    name = "mock"

    async def complete(self, model, messages, temperature, prompt_name) -> Completion:
        prompt = messages[0]["content"]
        fn = getattr(self, f"_{prompt_name}", None)
        data = fn(prompt) if fn else {"error": f"mock has no handler for {prompt_name}"}
        text = json.dumps(data, ensure_ascii=False)
        return Completion(text=text, tokens_in=len(prompt) // 4, tokens_out=len(text) // 4)

    # -- handlers -------------------------------------------------------------

    def _ping(self, p: str) -> dict:
        return {"ok": True, "word": "ready"}

    def _bible(self, p: str) -> dict:
        eps = re.findall(r"^E(\d+): (.*?) \|\| (.*)$", p, re.M)
        labels = re.findall(r"^(.+?) \| E(\d+) \| E(\d+) \| (\d+)$", _section(p, "SPEAKER LABELS", "POSSIBLE SPELLING"), re.M)
        chars = []
        for lab, a, b, n in labels:
            if int(n) < 2:
                continue
            u = lab.upper()
            who = "a woman" if re.search(r"LADY|MOTHER|DUCHESS|SISTER|QUEEN|GIRL|WOMAN|MAID|WIFE|AUNT|GOWN", u) else (
                "a man" if re.search(r"LORD|DUKE|MAN|FATHER|KING|GROOM|GUARD|BROTHER|SIR|PRIEST|NOBLEMAN", u) else "a figure")
            canonical = re.sub(r"^(LADY|LORD|THE)\s+", "", lab).title()
            chars.append({
                "canonical": canonical, "aliases": [{"label": lab, "first_ep": int(a), "last_ep": int(b)}],
                "role": f"[mock] speaks {n} lines between E{a} and E{b}",
                "visual_descriptor": f"{who} in period dress",
            })
        nums = [int(n) for n, _, _ in eps] or [1]
        lo, hi = min(nums), max(nums)
        q = max(1, (hi - lo + 1) // 4)
        arcs = [{"name": f"[mock] Part {k + 1}", "first_ep": lo + k * q, "last_ep": hi if k == 3 else lo + (k + 1) * q - 1,
                 "summary": dict((int(n), l) for n, l, _ in eps).get(lo + k * q, "")[:160]} for k in range(4) if lo + k * q <= hi]
        reveals = []
        for k, (n, logline, _) in enumerate(eps[:: max(1, len(eps) // 8)][:8], 1):
            reveals.append({"id": f"R{k:02d}", "description": "[mock] " + logline.split(".")[0][:140], "episode": int(n)})
        conflicts = []
        for line in _section(p, "POSSIBLE SPELLING CONFLICTS", "Return ONLY").splitlines():
            vs = re.findall(r"([A-Z][\w']+) \(E([\d, E]+)\)", line)
            if len(vs) >= 2:
                variants = [{"spelling": s, "episodes": [int(x) for x in re.findall(r"\d+", e)]} for s, e in vs]
                conflicts.append({"entity": vs[0][0], "variants": variants, "note": "[mock] code-detected; not judged"})
        return {"premise": "[mock] " + (eps[0][1] if eps else ""), "characters": chars, "arcs": arcs,
                "key_reveals": reveals, "continuity_conflicts": conflicts}

    def _lexicon(self, p: str) -> dict:
        tags = [t.strip() for t in p.split("TAGS:", 1)[-1].splitlines() if t.strip()]
        out = {}
        for t in tags:
            ar = 0.85 if re.search(HIGH, t) else 0.6 if re.search(MID, t) else 0.2 if re.search(LOW, t) else 0.4
            va = 0.6 if re.search(POS, t) else -0.6 if re.search(NEG, t) else 0.0
            out[t] = {"arousal": ar, "valence": va, "is_emotion": t not in TECHNIQUE}
        return {"tags": out}

    def _miner(self, p: str) -> dict:
        atoms = _atoms(_section(p, "ATOMS (id | start-end", "Return ONLY"))
        nxt = _atoms(_section(p, "NEXT EPISODE PEAK ATOMS", "TENSION HOTSPOTS"))
        hots = [(float(t), float(v)) for t, v in re.findall(r"^(\d+)s \| ([\d.]+) \|", _section(p, "TENSION HOTSPOTS", "EPISODE"), re.M)]
        dlg = [a for a in atoms if a["kind"] == "DLG"]
        if not dlg:
            return {"candidates": []}
        cands, used = [], set()

        def make(hook: dict, structure="straight", quiet=False, ff: dict | None = None):
            # Deliberately sloppy about length (up to ~70 s) like a real first draft,
            # so the validator's repair path is exercised in offline runs too.
            cliff = _cliff_for(dlg, hook, 26, 46 if quiet or ff else 70)
            if not cliff:
                return
            start = _cam_before(atoms, hook)
            window = " ".join(a["text"] for a in atoms if hook["start"] <= a["start"] <= cliff["end"])
            segs = [{"from_id": start["id"], "to_id": cliff["id"]}]
            hk = hook
            if ff:
                segs = [{"from_id": ff["id"], "to_id": ff["id"]}] + segs
                hk = ff
            cands.append({
                "structure": structure, "segments": segs, "hook_id": hk["id"], "cliffhanger_id": cliff["id"],
                "engine": _engine(window), "hook_text": hk["text"], "cliffhanger_text": cliff["text"],
                "why": "[mock] " + ("Quiet line away from the peaks; cuts before the answer." if quiet
                                    else "Opens on a charged line at a tension peak and cuts before the reply."),
                "miner_hook": 3 + ("!" in hk["text"]) + ("?" in hk["text"]), "miner_cliff": 3 + ("?" in cliff["text"]),
                "quiet_power": quiet,
            })
            used.add(hook["id"])

        for t, _v in sorted(hots, key=lambda h: -h[1])[:3]:
            near = sorted(dlg, key=lambda d: abs(d["start"] - (t - 1)))
            hook = next((d for d in near if d["id"] not in used), None)
            if hook:
                make(hook)
        quiet = [d for d in dlg if "!" not in d["text"] and all(abs(d["start"] - t) > 8 for t, _ in hots) and d["id"] not in used]
        if quiet:
            make(quiet[len(quiet) // 3], quiet=True)
        peaks = [a for a in nxt if a["kind"] == "DLG" and 1.2 <= a["end"] - a["start"] <= 4.0]
        if peaks and dlg:
            make(dlg[min(len(dlg) - 1, 2)], structure="flash_forward", ff=peaks[0])
        if len(cands) < 3:
            for d in dlg[::5]:
                if d["id"] not in used and len(cands) < 3:
                    make(d)
        return {"candidates": cands[:6]}

    def _miner_timestamps(self, p: str) -> dict:
        """Imitates an unchecked model: rounded times, loose quotes, lengths that drift."""
        atoms = _atoms(_section(p, "SCRIPT (id | start-end", "Return ONLY"))
        dlg = [a for a in atoms if a["kind"] == "DLG"]
        out = []
        for k, d in enumerate(dlg[2::6][:4]):
            start = round(d["start"] - 2.5)
            end = start + (35, 48, 64, 31)[k % 4]
            last = [x for x in dlg if x["end"] <= end] or [d]
            cliff = last[-1]["text"]
            out.append({"start": f"{start // 60:02d}:{start % 60:05.2f}", "end": f"{end // 60:02d}:{end % 60:05.2f}",
                        "hook_text": d["text"] if k % 2 == 0 else d["text"].strip('"').split(",")[0] + "...",
                        "cliffhanger_text": cliff, "engine": "other", "why": "[mock] timestamp-only baseline"})
        return {"candidates": out}

    def _arc_bridge(self, p: str) -> dict:
        blocks = re.split(r"^(?=C\d+-\d+ \| )", _section(p, "CANDIDATE MOMENTS", "Return ONLY"), flags=re.M)
        moments = []
        for b in blocks:
            head = re.match(r"(C\d+-\d+) \| E(\d+) \| .*? \| (\w+) \| hook: \"(.*)\" \| cliff: \"(.*)\"", b)
            if not head:
                continue
            atoms = _atoms(b)
            moments.append({"cid": head.group(1), "ep": int(head.group(2)), "engine": head.group(3),
                            "hook": head.group(4), "cliff": head.group(5), "atoms": atoms})
        out = []
        moments.sort(key=lambda m: m["ep"])
        for a in moments:
            for b in moments:
                if len(out) >= 4 or b["ep"] <= a["ep"] + 1 or a["engine"] != b["engine"]:
                    continue
                hook = next((x for x in a["atoms"] if x["text"] == a["hook"]), None)
                cliff = next((x for x in b["atoms"] if x["text"] == b["cliff"]), None)
                if not hook or not cliff:
                    continue
                a_end = [x for x in a["atoms"] if x["kind"] == "DLG" and 8 <= x["end"] - hook["start"] <= 16]
                if not a_end:
                    continue
                b_start = [x for x in b["atoms"] if x["kind"] == "CAM" and 12 <= cliff["end"] - x["start"] <= 24]
                if not b_start:
                    continue
                out.append({
                    "structure": "arc_bridge",
                    "segments": [{"from_id": _cam_before(a["atoms"], hook)["id"], "to_id": a_end[0]["id"]},
                                 {"from_id": b_start[0]["id"], "to_id": cliff["id"]}],
                    "hook_id": hook["id"], "cliffhanger_id": cliff["id"], "engine": a["engine"],
                    "hook_text": hook["text"], "cliffhanger_text": cliff["text"],
                    "why": f"[mock] Compresses E{a['ep']} into E{b['ep']} on one {a['engine']} promise.",
                    "miner_hook": 4, "miner_cliff": 4, "quiet_power": False,
                })
        return {"candidates": out}

    def _repair(self, p: str) -> dict:
        errors = _section(p, "failed these checks:", "Here is the candidate")
        try:
            cand = json.loads(_section(p, "relevant atoms again:\n", "\nATOMS:").strip())
        except json.JSONDecodeError:
            return {"drop": True, "reason": "[mock] could not read candidate"}
        atoms = _atoms(_section(p, "ATOMS:", ""))
        by = {a["id"]: a for a in atoms}
        if "unknown_id" in errors or "structure" in errors:
            return {"drop": True, "reason": "[mock] cannot repair structure"}
        hook = by.get(cand["hook_id"])
        if hook is None:
            return {"drop": True, "reason": "[mock] hook not found"}
        if "text_mismatch" in errors:
            cand["hook_text"] = hook["text"]
            if cand["cliffhanger_id"] in by:
                cand["cliffhanger_text"] = by[cand["cliffhanger_id"]]["text"]
        if "hook_late" in errors:
            cand["segments"][0]["from_id"] = cand["hook_id"]
        if "duration_out_of_range" in errors and len(cand["segments"]) == 1:
            same_ep = [a for a in atoms if a["id"].split(".")[0] == hook["id"].split(".")[0] and a["kind"] == "DLG"]
            cliff = _cliff_for(same_ep, hook, 28, 40)
            if not cliff:
                return {"drop": True, "reason": "[mock] no cliffhanger at a legal length"}
            cand["segments"][0]["to_id"] = cliff["id"]
            cand["cliffhanger_id"], cand["cliffhanger_text"] = cliff["id"], cliff["text"]
        return cand

    def _viewer_hook(self, p: str) -> dict:
        seen = _section(p, "first 3 seconds of a clip:", "Would you stop")
        kw = len(re.findall(r"slap|scream|blood|gasp|kneel|kiss|thief|marry|dead|poison|sword|crash|tear", seen, re.I))
        s = 1 + min(4, seen.count("!") + ("?" in seen) + kw + ('"' in seen))
        return {"stop": s, "reason": "[mock] " + ("a charged line right away" if s >= 4 else "slow, establishing opening")}

    def _viewer_full(self, p: str) -> dict:
        clip = _section(p, "speakers are described by how they look):", "Answer as a normal")
        lines = [ln for ln in clip.splitlines() if ln.strip()]
        speakers = Counter(re.match(r"^([^\[\"]+?):", ln).group(1) for ln in lines if re.match(r"^([^\[\"]+?):", ln))
        top = [s for s, _ in speakers.most_common(2)]
        last = next((ln for ln in reversed(lines) if '"' in ln), "")
        quoted = " ".join(re.findall(r'"[^"]*"', clip))
        names = sorted(set(re.findall(r"\b[A-Z][a-z]{3,}\b", quoted)))
        need = 2 + ("?" in last) + ("!" in " ".join(lines[-4:])) + (len(lines) > 12)
        return {
            "main_character": top[0] if top else "unclear",
            "what_they_want": "[mock] to be believed and get out of this moment",
            "conflict": f"[mock] pressure from {top[1]}" if len(top) > 1 else "[mock] unclear",
            "left_wondering": f"[mock] what comes after: {last.split(':', 1)[-1].strip()[:80]}",
            "need_next": min(5, need),
            "confusing": [n for n in names if n not in {"What", "Then", "Please", "There"}][:3],
        }

    def _grader(self, p: str) -> dict:
        truth = _section(p, "TRUTH", "CLIP:").lower()
        ans = json.loads(_section(p, "VIEWER ANSWERS:", "Return ONLY").strip() or "{}")
        mc = str(ans.get("main_character", "")).lower()
        who = 1 if mc and mc != "unclear" and any(w in truth for w in re.findall(r"woman|man|lady|lord", mc)) else 0
        return {"who": who, "want": 0.5, "conflict": 0.5 if "unclear" not in str(ans.get("conflict")) else 0,
                "open_question": 1 if ans.get("need_next", 1) >= 3 else 0, "notes": "[mock] keyword overlap only"}

    def _risk(self, p: str) -> dict:
        clip = _section(p, "CLIP (with atom IDs):", "KEY REVEALS")
        high = r"blood|torture|wound|gore|naked|corpse|brand(ed|ing)"
        low = r"slap|strike|kiss|threat|knife|sword|poison|hang|grab|seiz|drag|chain"
        cats, ev = set(), []
        for a in _atoms(clip):
            if re.search(high + "|" + low, a["text"], re.I):
                ev.append(a["id"])
                cats.add("graphic_violence" if re.search(r"blood|wound|gore|corpse|sword|knife|strike|slap", a["text"], re.I)
                         else "sexual_suggestive" if re.search(r"kiss|naked", a["text"], re.I) else "bullying")
        level = "high" if re.search(high, clip, re.I) else "low" if ev else "none"
        return {"policy_level": level, "categories": sorted(cats), "evidence_ids": ev[:6], "reveals_disclosed": []}
