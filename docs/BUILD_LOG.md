# Build log: where AI-generated work was wrong

Each entry: date, what was wrong, how it was caught, the fix. Honest and specific; no script text.

---

## 2026-11-08 · Phase 0 · Mock provider guessed list semantics
- **What was wrong:** the first draft of `MockProvider` tried to tell "a script of responses" apart from
  "a JSON array payload" by inspecting list element types. A list of dicts matched both cases, so a test
  scripting a bad reply then a good one could have returned the wrong item.
- **How caught:** self-review of `provider.py` before the tests ran.
- **Fix:** one rule with no guessing: a list is always a script of responses (the last one repeats); a JSON
  array payload must be passed as a string.

## 2026-11-08 · Phase 0 · Rate limiter tied to one event loop
- **What was wrong:** the limiter built its `asyncio.Semaphore`/`Lock` once. The sync `call()` wrapper runs a
  fresh event loop per call, and asyncio primitives that have waited are bound to their first loop, so the
  second sync call under contention could raise "bound to a different event loop".
- **How caught:** review while wiring `llm-ping` (three sync calls in a row).
- **Fix:** the limiter re-creates its primitives whenever the running loop changes.

## 2026-11-08 · Phase 1 · Spec's emotion-tag count includes technical tags
- **What was wrong:** the spec expects 621 distinct emotion tags. The parser finds 615.
- **How caught:** Phase 1 acceptance check. I re-counted under 8 splitting rules; 621 is reproduced exactly
  when (a) a parenthetical is not split on "/" and (b) O.S., V.O. and Off-screen are counted as emotions.
  Some pack headers mix a technical tag and emotions in one bracket, e.g. `(O.S. / <emotion>, <emotion>)`. The remaining gap of 1 is the tag "continuing", which the parser treats as CONT'D.
- **Fix:** none needed in code; the parser follows the spec's own rule (tech tags separate from emotions).
  The real-pack test asserts 615 with a pointer here. All other counts match exactly (50 episodes, 3,727
  blocks: 1,754 / 1,487 / 372 / 114; 0 blocks with end < start; 102.7 min; 33 weak openings).

## 2026-11-08 · Phase 1 · Invented fixture text collided with the real pack
- **What was wrong:** the "fake" 3-episode fixture I wrote shared 25 four-word sequences with the private
  pack. Most were generic shot vocabulary ("extreme close-up on the"), but 5 were story phrasing that
  happened to match (a stock retort, a plea, a description of a gloved hand, one parenthetical, one opener).
  Generated "original" text can echo source material it has seen in the same session.
- **How caught:** `scripts/check_leaks.py` run over the fixture before anything was staged.
- **Fix:** reworded the 5 story lines; added shot-type words to the checker's format vocabulary so generic
  camera language no longer counts. Fixture is now clean.

## 2026-11-08 · Phase 2 · The house name has more spellings than the brief says
- **What was wrong:** the brief lists five spellings of one noble house's name. The code detector (rapidfuzz ratio ≥ 75,
  same first three letters) found seven variants in one cluster. One extra variant (4 episodes) is clearly the same
  house: same manor, same family. The other (1 episode) is ambiguous. The heroine's family name, which shares the
  prefix, stays separate, grouped only with its own plural.
- **How caught:** Phase 2 acceptance check, then reading the surrounding lines locally (never copied into the repo).
- **Fix:** none needed in code. The detector proposes and the bible LLM decides. The private acceptance test reads the
  expected spellings from git-ignored `data/pack_expectations.yaml` and does not assume the list is complete.

## 2026-11-08 · Phase 3 · Spec's end-snap rule leaves a flash of the next shot
- **What was wrong:** "end at cliffhanger.end + 0.3 s" cut 0.2 s *into* the next shot whenever a shot change fell
  inside that pad. That is a 6-frame flash at the end of the promo, which an editor would reject.
- **How caught:** a hand-computed unit test (`test_end_never_leaks_next_line`) expected the cut on the shot change
  and got 17.2 s instead of 17.0 s.
- **Fix:** if a camera cut falls inside the pad, end on that cut. The other rules (extend to the shot end within
  1.5 s unless a new line starts first) are unchanged.

## 2026-11-08 · Phase 4 · Names in stage directions leaked to the Cold Viewer
- **What was wrong:** the brief replaces *speaker* names with visual descriptors, but camera and action lines also
  name characters ("Close-up on <name>'s face"). The "cold" viewer would have seen name cards a scroller never sees,
  which inflates comprehension scores.
- **How caught:** reading the perceived rendering of a fixture clip while writing `render.py`.
- **Fix:** `render.unname()` swaps canonical and given names in camera, action and SFX text for "the <descriptor>".
  Names spoken in dialogue are kept, because viewers hear them. Covered by a test.

## 2026-11-08 · Phase 3 · Rate limiter throttled the offline mock
- **What was wrong:** the mock provider went through the 10-requests-per-minute limiter, so an offline run of a
  3-episode fixture took 101 s, almost all of it waiting.
- **How caught:** stage timings in the first `--provider mock` run (viewer 54 s, risk 30 s for 26 fake calls).
- **Fix:** only live calls take a rate-limit token; mock calls skip it. Full-pack mock run: 2.5 s.

## 2026-11-08 · Phase 4 · Test run wrote into the real lexicon store
- **What was wrong:** the cross-show lexicon store defaulted to `./.cache/lexicon/`, whatever cache the client used.
  A test's fake model wrote ratings into the repo's real store, and the replay test then saw one call fewer.
- **How caught:** `test_replay_reproduces_offline` failed (21 vs 22 calls).
- **Fix:** the store lives beside the client's response cache. The test now asserts what matters: every replayed
  call is a cache hit and the slate is identical.

## 2026-11-08 · Prompt change · Miner can now see next-episode atoms
- **What changed:** `miner.md` gained `{{next_peaks}}`, the atoms around the next episode's top three hotspots.
- **Why:** the brief allows flash-forwards from the next episode, but the miner only saw this episode's atom IDs, so
  it could only invent next-episode IDs (which validation would reject as `unknown_id`).

## 2026-11-08 · Phase 6 · Progress stream ended before the run finished
- **What was wrong:** the first SSE design replayed a snapshot of past events and *also* drained a queue holding the
  same events, so the stream could end early. Separately, the background job was started without keeping a reference
  to its task.
- **How caught:** `test_upload_runs_a_mock_job` never saw the slate stage. Printing the events showed a
  cancellation. Part of the cause was the test harness: FastAPI's TestClient without a `with` block starts a fresh
  event loop per request and tears it down afterwards.
- **Fix:** one append-only event list per job that each subscriber reads by index; the task reference is kept; the
  test uses a single client session (as uvicorn does). Cancellation is now reported as an error event.

## 2026-11-08 · Phase 6 · Brand red failed contrast as text
- **What was wrong:** the brief's peak red (#E8494F) measures 4.29:1 on the app background and 3.66:1 on panels, below
  the 4.5:1 AA floor for body text. The light theme's deepened amber was 4.08:1.
- **How caught:** axe-core run against every screen in both themes (2 to 23 contrast violations per screen).
- **Fix:** kept #E8494F for data marks and added a text-only `rec-ink` (#F46A6F, 4.76:1). The light theme uses deeper
  amber and teal. The phone animatic keeps dark tokens in light mode because it depicts a screen. 0 violations after.

## 2026-11-08 · Phase 6 · Slate rationale called tied scores "the best"
- **What was wrong:** the rationale picked each promo's strongest score by index rank, so when half the pool shared a
  value every one of them was described as "best … of all 130 promos".
- **How caught:** reading the Slate screenshot during the self-critique pass.
- **Fix:** rank = (strictly better + half the ties) / pool; "best" only when the score is unique.

## 2026-11-08 · Phase 6 · Sentence-initial words flagged as unexplained names
- **What was wrong:** the proper-noun detector keeps capitalised words never seen in lower case. On a small show,
  sentence-initial words such as "Search" and "Smile" qualified, and the referent check then penalised promos for
  "names a stranger cannot place". Kinship words ("Father") were flagged too.
- **How caught:** badges on the invented demo show's Slate screen; the Quality gallery listed "Father".
- **Fix:** a capital at the start of a sentence no longer counts as evidence of a name unless it is a known
  character, and kinship and title words are excluded.

## 2026-11-08 · Phase 7 · Spec asks for a public Space but private demo data in the image
- **What was wrong:** the brief ships the demo run inside the image, but a public Space's repository (and so the image
  build context) is public, which would publish the client's scripts.
- **How caught:** planning the Docker build against the confidentiality rule.
- **Fix:** the image holds no runs; `scripts/fetch_demo.py` downloads them at start from a private dataset
  (`DEMO_RUNS_URL` + `HF_TOKEN` secrets), and the access code still guards them.

## 2026-11-08 · Phase 7 · Show names slipped into committable files
- **What was wrong:** I wrote real character and house names from the pack into a docstring, two build-log entries
  and a pack test. None were full script lines, so the 4-word leak hook could not catch them, but the master prompt
  is git-ignored precisely because it names such details.
- **How caught:** a deliberate grep for show names across every committable file before the final review.
- **Fix:** replaced them with invented names or generic wording, moved the pack test's expected spellings into
  git-ignored `data/pack_expectations.yaml`, and extended `check_leaks.py` to block the pack's distinctive proper
  nouns (capitalised in the story text, never lower case, not in the system word list; 39 names).

## 2026-11-09 · Hardening · Runs started with your own key could not be opened
- **What was wrong:** the API returned a run token for runs started with the user's own API key, but the UI threw it
  away. Without the access code, "Open the tension map" led straight back to the gate.
- **How caught:** an authentication review of every path a visitor without the code can take.
- **Fix:** the server now returns a signed session scoped to that one run, and the UI keeps it for the tab. A test
  checks that this session opens its own run and no other.

## 2026-11-09 · Hardening · The access code was not active until the config was first read
- **What was wrong:** `.env` was loaded inside `load_config`. On a local server started from `.env`, requests that
  arrived before any config read saw no access code and were served openly.
- **How caught:** probing a freshly restarted server with curl while reviewing authentication.
- **Fix:** the server loads `.env` at import; a regression test pins it.

## 2026-11-09 · Hardening · The access code travelled in URLs
- **What was wrong:** EDL and CSV downloads used `?code=` so that a plain link worked. Query strings are written to
  server access logs and browser history.
- **How caught:** the same review.
- **Fix:** the code is exchanged once for an 8-hour HMAC-signed bearer token. Downloads and the progress stream now
  use `fetch` with headers, and the server ignores credentials in query strings (tested). Rotating the code revokes
  every session.

## 2026-11-09 · Setup · Newest model was unusable on the free tier
- **What was wrong:** I first configured the miner as the newest Flash model recommended by the docs. Its ping took
  146 s, and a direct call with retries off timed out after 90 s, while the previous stable Flash answered in 2.4 s.
- **How caught:** latency column of `coldopen llm-ping`, then a one-call timing check per model.
- **Fix:** the miner uses the previous stable Flash model; the reason is noted in `config.yaml`.

## 2026-11-09 · Live run · Every arc bridge failed the schema
- **What was wrong:** the arc-bridge prompt said "same fields as before", but it is a separate conversation, so the
  model had never seen those fields. It invented its own shape, including start and end *times*, which the core rule
  forbids. All 3 arc bridges in the first live 3-episode run were rejected as `schema`.
- **How caught:** reading `05_validation_log.jsonl` and the raw call in `llm_calls/` after the first live run.
- **Fix:** the prompt now spells out the exact JSON, the ID-only rule, the hook and cliffhanger rules and the engine
  list. Mock runs never caught this because MockBrain answers in the right shape by construction.

## 2026-11-09 · Live run · One overloaded model ended the whole run
- **What was wrong:** the first full-pack run stopped at its first call. The provider answered 503 "high demand" six
  times over about two minutes, and the retry policy then re-raised, so one demand spike ended a run of more than an
  hour.
- **How caught:** the ablation run exited with code 1; the log showed `ServiceUnavailableError` at the bible stage.
- **Fix:** `fallbacks:` in `config.yaml` names models to try after retries run out. Each call record keeps both
  `model` and `answered_by`, and the run report notes every fallback, so no answer hides where it came from. A test
  covers the path. Rate limits are now per provider, so a slow Gemini bucket no longer holds up Groq.

## 2026-11-09 · Live run · Free-tier daily quota stopped the full run
- **What was wrong:** the second full run stopped at mining. The best Gemini Flash model allows 20 requests a day on
  a free key, and test runs had used them. Three things made it worse. LiteLLM reported the quota as a 400, so the
  fallback did not fire. The retry policy would have waited about two minutes per call on a quota that resets
  daily. And nothing downstream could continue without every call succeeding.
- **How caught:** the run log showed `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20, with a retry
  delay of almost two hours.
- **Fix:**
  - A typed `ProviderLimitError` says which limit was hit and when it resets.
  - Daily quotas skip the back-off and move straight to the next model.
  - Fallback models with separate quotas are listed for every role.
  - Per-item stages finish with counted, labelled partial results, and the UI shows a "Partial run" banner.
  - The CLI prints a plain pause message (exit code 2) instead of a traceback.
  - Server-key runs are capped at 3 episodes so a visitor cannot drain the shared quota.
  - Tests cover a limit hit during mining and during the Cold Viewer stage.
- **Rejected alternative:** mining on Groq. A mining call is about 12k tokens and an arc bridge about 22k, which is
  over Groq's 8k-tokens-per-minute free limit for a single request.

## 2026-11-09 · Demo video · A recording almost showed the client's show title
- **What was wrong:** the first public walkthrough was recorded while signed in. Its opening frames listed every run,
  including the title of the client's show, and it was saved under `docs/` to be committed. The text leak guard
  cannot read video.
- **How caught:** reviewing frames extracted from the video before committing it.
- **Fix:** deleted it and re-recorded the public video signed out, so only the invented sample show appears.
  `record_demo.py` now refuses to save a signed-in recording anywhere git would track it.
