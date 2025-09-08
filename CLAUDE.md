# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

music-claude is an LLM-powered music library organizer built with a four-stage classification pipeline. It uses OpenAI models for intelligent music categorization and organization, with comprehensive caching and error handling.

## Architecture

### Four-Stage Processing Pipeline
1. **Triage & Pre-Processing** (`pipeline/album_orchestrator.py`): Audio format validation, metadata extraction, cache checking
2. **Structured Data Extraction** (`pipeline/album_stages.py`): Uses gpt-4o-mini for fast artist/album/year extraction  
3. **Semantic Enrichment**: Uses gpt-4o for advanced musical knowledge (genres, moods, instrumentation)
4. **Canonicalization & Validation**: Fact-checking, normalization, format tag extraction

### Core Components
- **API Client** (`api/client.py`): OpenAI integration with exponential backoff, JSON repair, error handling
- **Schemas** (`api/schemas.py`): Pydantic models for AlbumInfo, EnrichedAlbumInfo, and API responses
- **Caching** (`caching/cache_manager.py`): Two-tier caching (SQLite execution cache + JSON API cache)
- **File Operations** (`filesystem/file_ops.py`): Cross-platform pathlib operations with UTF-8 support
- **Album Detection** (`filesystem/album_detector.py`): Identifies album boundaries from music files
- **Configuration** (`utils/config_loader.py`): YAML config with environment variable overrides
- **Logging** (`utils/logging_config.py`): Structured logging with file rotation and progress tracking

### Music Categories
Organized into top-level buckets: Classical, Electronic, Jazz, Compilations & VA, Soundtracks (Film/TV/Games/Anime & Ghibli/Stage & Musicals), Library, Misc

## Development Commands

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Set OpenAI API key
export OPENAI_API_KEY="your-api-key-here"
```

### Running the Application
```bash
# Analyze music library (planning mode)
python main.py /path/to/music

# Execute organization plan
python main.py /path/to/music --execute

# Process limited files for testing
python main.py /path/to/music --limit 50

# Use heuristics only (no LLM calls)
python main.py /path/to/music --no-llm

# Enable verbose logging
python main.py /path/to/music --verbose
```

### Testing
```bash
# Run regression tests (fast, no LLM calls)
python tools/check_regressions.py
```

### Configuration
- Main config: `config.yaml` 
- Override with environment variables: `MUSIC_CLAUDE_API__MAX_RETRIES=5`
- Test cases: `tests/regression_album_cases.txt`

## Key Implementation Details

- **UTF-8 Mode**: Enabled via `PYTHONUTF8=1` for universal file compatibility
- **Concurrent Processing**: ThreadPoolExecutor with configurable worker limits
- **Format Detection**: Recognizes XRCD, K2HD, SACD, DSD, and other audiophile formats  
- **Error Handling**: Structured exception hierarchy extending from `MusicOrganizerError`
- **Output Location**: Results saved to `<music_dir>/_music_claude_output/`

## Working with the Codebase

- The main entry point is `main.py`
- Stage implementations are in `pipeline/album_stages.py` 
- The orchestrator (`pipeline/album_orchestrator.py`) coordinates the full pipeline
- Configuration is centralized in `config.yaml` with strong typing via Pydantic
- All file operations use pathlib for cross-platform compatibility
- Regression cases in `tests/regression_album_cases.txt` define expected classifications