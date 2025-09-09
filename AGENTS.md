# Repository Guidelines

## Project Structure & Module Organization
- `main.py`: CLI entry point.
- `api/`: OpenAI client (`client.py`) and Pydantic schemas (`schemas.py`).
- `pipeline/`: Album pipeline orchestrator and stages (`album_orchestrator.py`, `album_stages.py`).
- `filesystem/`: Pathlib-based file discovery and safe moves.
- `caching/`: SQLite execution cache and JSON API cache (`cache_manager.py`).
- `utils/`: Config loader, logging, and exception hierarchy.
- `tools/`: Developer utilities (e.g., `tools/check_regressions.py`).
- `tests/`: Regression cases (`tests/regression_album_cases.txt`).

## Build, Test, and Development Commands
- Install deps: `pip install -r requirements.txt`
- Run (plan only): `python main.py /path/to/music`
- Execute plan: `python main.py /path/to/music --execute`
- Fast smoke test: `python main.py /path/to/music --limit 50 --no-llm --verbose`
- Regression check: `python tools/check_regressions.py` (requires `OPENAI_API_KEY`).

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and type hints.
- Use `pathlib` for paths and `utils.logging_config` for logging (prefer `LoggerMixin`/`get_logger`).
- Modules and functions: snake_case. Classes: PascalCase. Constants: UPPER_SNAKE.
- Validate new structured data via Pydantic models in `api/schemas.py`.

## Testing Guidelines
- Primary suite: `tools/check_regressions.py` against `tests/regression_album_cases.txt`.
- Case format: `Artist - Album => Category[/Subcategory]` (e.g., `Nobuo Uematsu - FFVII => Soundtracks/Games`).
- Keep cases focused and deterministic; prefer representative albums over large lists.
- For PRs, run the regression check and include pass/fail counts.

## Commit & Pull Request Guidelines
- Use Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
  - Examples from history: `feat: implement tiered fallback`, `fix: resolve token limit issue`.
- PRs must include: summary, motivation, key changes, sample command(s), and relevant log/output snippet (e.g., path to `_music_claude_output`).
- Update `README.md`/`config.yaml` when changing behavior or config keys; adjust schemas if models change.
- Do not commit secrets, large media, or generated outputs.

## Security & Configuration Tips
- Set `OPENAI_API_KEY` via environment; never hardcode. Example: `export OPENAI_API_KEY=...`.
- Override YAML config via env, e.g., `MUSIC_CLAUDE_API__MAX_RETRIES=5`.
- Outputs live under `<music_dir>/_music_claude_output/`; keep these out of VCS.
