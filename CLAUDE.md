# Cold Open: conventions

Reads a microdrama screenplay (folder of timestamped episode scripts) and returns a ranked launch slate
of 20-60 s promos (hook in first 3 s, cliffhanger ending, makes sense to a stranger) plus EDL/CSV cut lists.

**Rule: AI judges meaning. Code owns facts. The editor makes the final call.**
- LLM returns atom IDs + short reasons only. Never timestamps, durations or ranks.
- Code resolves IDs to times, enforces every hard constraint, scores and ranks. Humans approve in the UI.

Working rules
- Build in phases (PROMPT.md section 17). Stop after each phase, run its acceptance checks, wait for review.
- Never invent data (model names, prices, numbers): leave a marked `TODO` and say so.
- Confidential: never commit `data/`, `runs/`, `.cache/`, `PROMPT.md`; never copy script lines into code, tests,
  docs or commit messages. Real-pack tests use `@pytest.mark.pack` and skip without `data/pack/`.
  Public tests use the invented show in `tests/fixtures/mini_show/`. `scripts/check_leaks.py` guards commits.
- Log every AI-generated mistake (what, how caught, fix) in `docs/BUILD_LOG.md`.
- pytest green at the end of every phase. Type hints, Pydantic contracts, docstrings that explain *why*.
- All LLM calls go through `coldopen/llm` (`LLMClient.call(role, prompt_name, variables, schema)`); roles map
  to models in `config.yaml`. Prompts live in `coldopen/prompts/*.md` with `{{var}}` placeholders.
- Ask before: paid services, data-contract changes (`coldopen/models.py`), gold labels.

Run: `uv sync`, `uv run pytest`, `uv run coldopen --help`. Offline: `--provider mock`. UI: `cd web && npm run build`.
