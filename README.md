# music-claude: LLM-Powered Music Library Organizer

A robust, intelligent music library organizer built with a four-stage LLM pipeline following enterprise-grade architectural patterns.

## Features

### 🎵 **Intelligent Classification**
- **Four-Stage Pipeline**: Triage → Extraction → Enrichment → Canonicalization
- **Dynamic Model Selection**: Uses `gpt-4o-mini` for extraction, `gpt-4o` for enrichment
- **Semantic Understanding**: Infers genres, moods, instrumentation, and use cases

### 🚀 **Performance & Reliability**
- **Multi-Layer Caching**: Execution cache (SQLite) + API cache (JSON) for speed
- **Concurrent Processing**: ThreadPoolExecutor with configurable worker count
- **Resilient API Client**: Exponential backoff, JSON repair, comprehensive error handling

### 🛠️ **Enterprise-Grade Architecture**
- **Cross-Platform**: pathlib-based filesystem operations with UTF-8 support
- **Structured Logging**: Configurable levels, file rotation, progress tracking
- **Configuration Management**: YAML-based config with environment variable overrides
- **Exception Hierarchy**: Precise error handling and debugging

### 📁 **Smart Organization**
- **Category Classification**: Classical, Jazz, Electronic, Soundtracks, Library, etc.
- **Format Recognition**: XRCD, K2HD, SACD, DSD, and other audiophile formats
- **Safe Operations**: Duplicate detection, unique path generation, rollback support

## Quick Start

### Installation

```bash
# Clone or download the music-claude directory
cd music-claude

# Install dependencies
pip install -r requirements.txt

# Set your OpenAI API key
export OPENAI_API_KEY="your-api-key-here"
```

### Basic Usage

```bash
# Analyze your music library (planning mode)
python main.py /path/to/your/music

# Execute the organization plan
python main.py /path/to/your/music --execute

# Process with limited files for testing
python main.py /path/to/your/music --limit 50

# Use heuristics only (no LLM calls)
python main.py /path/to/your/music --no-llm

# Enable verbose logging
python main.py /path/to/your/music --verbose
```

## Architecture Overview

### Four-Stage Pipeline

1. **Stage 1: Triage & Pre-Processing**
   - Validates audio formats using mutagen
   - Extracts existing metadata tags
   - Checks execution cache for already-processed files

2. **Stage 2: Structured Data Extraction**
   - Uses `gpt-4o-mini` for fast, cost-effective parsing
   - Extracts artist, title, album, year from filenames/metadata
   - Handles various naming conventions and formats

3. **Stage 3: Semantic Enrichment**
   - Uses `gpt-4o` for advanced musical knowledge
   - Infers genres, moods, instrumentation, energy levels
   - Suggests appropriate listening occasions

4. **Stage 4: Canonicalization & Validation**
   - Fact-checks against music databases (MusicBrainz/Discogs)
   - Normalizes artist/album names
   - Extracts format tags and validates metadata

### Configuration

Customize behavior via `config.yaml`:

```yaml
api:
  openai_model_extraction: "gpt-4o-mini"
  openai_model_enrichment: "gpt-4o"
  max_retries: 3

concurrency:
  max_workers: 4
  api_concurrency: 2

caching:
  cache_expiry_days: 30

filesystem:
  audio_extensions: [".flac", ".mp3", ".m4a", ...]
  ignored_dirs: ["covers", "artwork", "scans"]
```

Override with environment variables:
```bash
export MUSIC_CLAUDE_API__MAX_RETRIES=5
export MUSIC_CLAUDE_CONCURRENCY__MAX_WORKERS=8
export MUSIC_CLAUDE_LOGGING__LEVEL=DEBUG
```

## Advanced Features

### Caching System

- **L1 Execution Cache**: SQLite database tracks processed files by path/timestamp
- **L2 API Cache**: JSON cache stores LLM responses to avoid duplicate API calls
- **Automatic Cleanup**: Configurable expiry and cleanup of old cache entries

### Error Handling

```python
# Custom exception hierarchy
MusicOrganizerError
├── FileProcessingError
│   ├── UnsupportedFormatError
│   └── MetadataExtractionError
├── MetadataPipelineError
│   ├── APICommunicationError
│   ├── APISchemaError
│   └── CanonicalizationError
└── CacheError
```

### Logging

```python
# Built-in progress tracking
logger.info("Processed 250/1000 files (25.0%)")

# Structured error messages
logger.error("Stage 2 failed for /music/song.mp3: Schema validation error")

# Performance metrics
logger.info("Processing complete. API calls: 45, Cache hits: 78, Success rate: 95.2%")
```

## Output Files

The organizer generates detailed reports in `<music_dir>/_music_claude_output/`:

- `organization_plan.csv`: Complete file mapping with reasons
- `processing_report.json`: Detailed statistics and results
- `failed_files.txt`: List of files that couldn't be processed
- `music-claude.log`: Comprehensive processing log

## Supported Audio Formats

- **Lossless**: FLAC, APE, WavPack, WAV, AIFF
- **Lossy**: MP3, M4A, OGG, Opus
- **High-Resolution**: DSD (.dsf, .dff)

## Best Practices

1. **Start Small**: Use `--limit 100` for initial testing
2. **Review First**: Always run without `--execute` to review the plan
3. **Backup**: Keep backups of your music library before executing
4. **Monitor Logs**: Use `--verbose` to track progress and issues
5. **Configure Caching**: Adjust cache expiry based on your library update frequency

## Troubleshooting

### Common Issues

- **API Rate Limits**: Reduce `api_concurrency` in config
- **Memory Usage**: Lower `max_workers` for large libraries  
- **Slow Processing**: Check cache hit rates and API response times
- **File Permissions**: Ensure read/write access to music directory

### Debug Mode

```bash
export MUSIC_CLAUDE_LOGGING__LEVEL=DEBUG
python main.py /path/to/music --verbose
```

## Architecture Benefits

This implementation follows the architectural principles from your detailed report:

✅ **Cross-platform pathlib operations**  
✅ **UTF-8 mode for universal compatibility**  
✅ **Structured exception hierarchy**  
✅ **Multi-stage pipeline with model routing**  
✅ **Resilient API client with JSON repair**  
✅ **Multi-layer caching system**  
✅ **Concurrent processing with rate limiting**  
✅ **Comprehensive logging and monitoring**  

The result is a production-ready music organizer that's robust, efficient, and maintainable.

## License

This implementation is provided as an example following your architectural specification.