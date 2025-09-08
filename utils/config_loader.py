"""
Configuration management for the music organizer.

This module handles loading and validating configuration from YAML files
with sensible defaults and environment variable support.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from utils.exceptions import ConfigurationError


@dataclass
class MusicConfig:
    """Structured configuration class with defaults."""
    
    # API Configuration
    openai_model_extraction: str = "gpt-4o-mini"
    openai_model_enrichment: str = "gpt-4o"
    max_retries: int = 3
    timeout_seconds: float = 30.0
    
    # Caching Configuration
    execution_cache_file: str = "~/.cache/music-claude/execution.db"
    api_cache_file: str = "~/.cache/music-claude/api.json"
    cache_expiry_days: int = 30
    
    # Concurrency Configuration
    max_workers: int = 4
    api_concurrency: int = 2
    
    # Filesystem Configuration
    audio_extensions: list = field(default_factory=lambda: [
        '.flac', '.mp3', '.m4a', '.wav', '.aiff', '.ogg', '.opus', '.ape', '.wv', '.dsf', '.dff'
    ])
    ignored_dirs: list = field(default_factory=lambda: [
        'covers', 'artwork', 'scans', 'booklet', '@eadir'
    ])
    
    # Logging Configuration
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: str = "music-claude.log"
    
    # Music Organization Categories
    top_buckets: list = field(default_factory=lambda: [
        'Classical', 'Electronic', 'Jazz', 'Compilations & VA', 'Soundtracks', 'Library', 'Misc'
    ])
    soundtrack_subs: list = field(default_factory=lambda: [
        'Film', 'TV', 'Games', 'Anime & Ghibli', 'Stage & Musicals'
    ])
    
    # Format Tags
    format_tags: dict = field(default_factory=lambda: {
        'XRCD24': ['xrcd24', 'xr-cd24', 'xrcd 24', 'xr24'],
        'XRCD2': ['xrcd2', 'xrcd 2'],
        'XRCD': ['xrcd'],
        'K2HD': ['k2hd', 'k2 hd'],
        'K2': ['k2', 'k2 mastering'],
        'SHM-CD': ['shm-cd', 'shm cd', 'platinum shm-cd'],
        'MFSL': ['mfsl', 'mobile fidelity', 'udcd'],
        'DCC': ['dcc', 'dcc gold'],
        'HDCD': ['hdcd'],
        'SACD': ['sacd'],
        'DSD': ['dsd'],
        '24K-Gold': ['24k gold', '24kt gold', '24 kt gold', 'gold 24k'],
        '24-88': ['24-88', '24/88', '24bit-88khz'],
        '24-96': ['24-96', '24/96', '24bit-96khz']
    })


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load configuration from YAML file with defaults and environment variable support.
    
    Args:
        config_path: Path to configuration file (optional)
        
    Returns:
        Configuration dictionary
        
    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Start with default configuration
    default_config = MusicConfig()
    config_dict = _dataclass_to_dict(default_config)
    
    # Load from file if provided
    if config_path and config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f)
                if file_config:
                    config_dict = _merge_configs(config_dict, file_config)
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in config file: {e}")
        except IOError as e:
            raise ConfigurationError(f"Cannot read config file {config_path}: {e}")
    
    # Override with environment variables
    config_dict = _apply_env_overrides(config_dict)
    
    # Validate configuration
    _validate_config(config_dict)
    
    return config_dict


def _dataclass_to_dict(obj) -> Dict[str, Any]:
    """Convert dataclass to dictionary recursively."""
    if hasattr(obj, '__dataclass_fields__'):
        result = {}
        for field_name in obj.__dataclass_fields__:
            value = getattr(obj, field_name)
            result[field_name] = _dataclass_to_dict(value)
        return result
    elif isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(item) for item in obj]
    else:
        return obj


def _merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge configuration dictionaries.
    
    Args:
        base: Base configuration dictionary
        override: Override configuration dictionary
        
    Returns:
        Merged configuration dictionary
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_configs(result[key], value)
        else:
            result[key] = value
    
    return result


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply environment variable overrides to configuration.
    
    Environment variables should be prefixed with MUSIC_CLAUDE_ and use
    double underscores to represent nested keys.
    
    Examples:
        MUSIC_CLAUDE_API__MAX_RETRIES=5
        MUSIC_CLAUDE_LOGGING__LEVEL=DEBUG
    """
    prefix = "MUSIC_CLAUDE_"
    
    for env_var, value in os.environ.items():
        if not env_var.startswith(prefix):
            continue
        
        # Parse the key path
        key_path = env_var[len(prefix):].lower().split('__')
        
        # Convert value to appropriate type
        converted_value = _convert_env_value(value)
        
        # Set the value in the config dictionary
        _set_nested_value(config, key_path, converted_value)
    
    return config


def _convert_env_value(value: str) -> Any:
    """Convert environment variable string to appropriate Python type."""
    # Boolean values
    if value.lower() in ('true', 'yes', '1', 'on'):
        return True
    elif value.lower() in ('false', 'no', '0', 'off'):
        return False
    
    # Integer values
    try:
        return int(value)
    except ValueError:
        pass
    
    # Float values
    try:
        return float(value)
    except ValueError:
        pass
    
    # JSON/List values (if starts with [ or {)
    if value.startswith(('[', '{')):
        try:
            import json
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    
    # String value (default)
    return value


def _set_nested_value(config: Dict[str, Any], key_path: list, value: Any):
    """Set a value in a nested dictionary using a list of keys."""
    current = config
    
    # Navigate to the parent of the target key
    for key in key_path[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    
    # Set the final value
    current[key_path[-1]] = value


def _validate_config(config: Dict[str, Any]):
    """
    Validate configuration values.
    
    Args:
        config: Configuration dictionary to validate
        
    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Validate API configuration
    api_config = config.get('api', {})
    
    max_retries = api_config.get('max_retries', 3)
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ConfigurationError("api.max_retries must be a non-negative integer")
    
    timeout_seconds = api_config.get('timeout_seconds', 30.0)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ConfigurationError("api.timeout_seconds must be a positive number")
    
    # Validate concurrency configuration
    concurrency_config = config.get('concurrency', {})
    
    max_workers = concurrency_config.get('max_workers', 4)
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ConfigurationError("concurrency.max_workers must be a positive integer")
    
    api_concurrency = concurrency_config.get('api_concurrency', 2)
    if not isinstance(api_concurrency, int) or api_concurrency < 1:
        raise ConfigurationError("concurrency.api_concurrency must be a positive integer")
    
    # Validate filesystem configuration
    filesystem_config = config.get('filesystem', {})
    
    audio_extensions = filesystem_config.get('audio_extensions', [])
    if not isinstance(audio_extensions, list) or not audio_extensions:
        raise ConfigurationError("filesystem.audio_extensions must be a non-empty list")
    
    # Validate logging configuration
    logging_config = config.get('logging', {})
    
    log_level = logging_config.get('level', 'INFO')
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if log_level.upper() not in valid_levels:
        raise ConfigurationError(f"logging.level must be one of {valid_levels}")
    
    # Validate categories
    categories_config = config.get('categories', {})
    
    top_buckets = categories_config.get('top_buckets', [])
    if not isinstance(top_buckets, list) or not top_buckets:
        raise ConfigurationError("categories.top_buckets must be a non-empty list")
    
    # Validate caching configuration
    caching_config = config.get('caching', {})
    
    expiry_days = caching_config.get('cache_expiry_days', 30)
    if not isinstance(expiry_days, int) or expiry_days < 1:
        raise ConfigurationError("caching.cache_expiry_days must be a positive integer")


def get_config_template() -> str:
    """
    Get a YAML template for the configuration file.
    
    Returns:
        YAML configuration template as string
    """
    return """# Configuration for music-claude organizer
api:
  openai_model_extraction: "gpt-4o-mini"  # Fast model for data extraction
  openai_model_enrichment: "gpt-4o"       # Powerful model for semantic enrichment
  max_retries: 3
  timeout_seconds: 30

caching:
  execution_cache_file: "~/.cache/music-claude/execution.db"
  api_cache_file: "~/.cache/music-claude/api.json"
  cache_expiry_days: 30

concurrency:
  max_workers: 4
  api_concurrency: 2

filesystem:
  audio_extensions:
    - .flac
    - .mp3
    - .m4a
    - .wav
    - .aiff
    - .ogg
    - .opus
    - .ape
    - .wv
    - .dsf
    - .dff

  ignored_dirs:
    - covers
    - artwork
    - scans
    - booklet
    - "@eadir"

logging:
  level: INFO
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file: "music-claude.log"

# Music organization categories
categories:
  top_buckets:
    - Classical
    - Electronic
    - Jazz
    - "Compilations & VA"
    - Soundtracks
    - Library
    - Misc

  soundtrack_subs:
    - Film
    - TV
    - Games
    - "Anime & Ghibli"
    - "Stage & Musicals"

# Format tags with preference ordering
format_tags:
  XRCD24: ["xrcd24", "xr-cd24", "xrcd 24", "xr24"]
  XRCD2: ["xrcd2", "xrcd 2"]
  XRCD: ["xrcd"]
  K2HD: ["k2hd", "k2 hd"]
  K2: ["k2", "k2 mastering"]
  SHM-CD: ["shm-cd", "shm cd", "platinum shm-cd"]
  MFSL: ["mfsl", "mobile fidelity", "udcd"]
  DCC: ["dcc", "dcc gold"]
  HDCD: ["hdcd"]
  SACD: ["sacd"]
  DSD: ["dsd"]
  "24K-Gold": ["24k gold", "24kt gold", "24 kt gold", "gold 24k"]
  "24-88": ["24-88", "24/88", "24bit-88khz"]
  "24-96": ["24-96", "24/96", "24bit-96khz"]
"""