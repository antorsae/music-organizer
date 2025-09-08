#!/usr/bin/env python3
"""
music-claude: An LLM-Powered Music Library Organizer

This is a robust, multi-stage music classification and organization system
built following the architectural principles outlined in your detailed report.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

# Enable UTF-8 mode for universal file compatibility
if not os.environ.get('PYTHONUTF8'):
    os.environ['PYTHONUTF8'] = '1'

from utils.logging_config import setup_logging
from utils.config_loader import load_config
from pipeline.album_orchestrator import AlbumMusicPipeline
from utils.exceptions import MusicOrganizerError


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Intelligent music library organizer using LLM classification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/music                    # Analyze and plan organization
  %(prog)s /path/to/music --execute          # Execute the organization plan
  %(prog)s /path/to/music --limit 100        # Process only 100 albums for testing
  %(prog)s /path/to/music --no-llm           # Use heuristics only (faster)
        """
    )
    
    parser.add_argument(
        "music_directory",
        type=Path,
        help="Path to the music library root directory"
    )
    
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config file (default: ./config.yaml)"
    )
    
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the organization plan (default: plan only)"
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit processing to N albums (for testing)"
    )
    
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM classification, use heuristics only"
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5",
        choices=["gpt-4o", "gpt-4o-mini", "gpt-5", "gpt-5-mini", "gpt-5-nano", "claude-3-5-sonnet"],
        help="LLM model to use for classification (default: gpt-5)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for output files (default: <music_dir>/_music_claude_output)"
    )

    return parser.parse_args()


def validate_music_directory(path: Path) -> None:
    """Validate that the music directory exists and is accessible."""
    if not path.exists():
        raise MusicOrganizerError(f"Music directory does not exist: {path}")
    
    if not path.is_dir():
        raise MusicOrganizerError(f"Music path is not a directory: {path}")
    
    if not os.access(path, os.R_OK):
        raise MusicOrganizerError(f"Cannot read music directory: {path}")


def main() -> int:
    """Main entry point."""
    try:
        args = parse_arguments()
        
        # Resolve paths
        music_dir = args.music_directory.resolve()
        
        # Default config path is in the same directory as this script
        if args.config:
            config_path = args.config
        else:
            script_dir = Path(__file__).parent
            config_path = script_dir / "config.yaml"
            
        output_dir = args.output_dir or music_dir / "_music_claude_output"
        
        # Validate inputs
        validate_music_directory(music_dir)
        
        # Load configuration
        print(f"Loading config from: {config_path}")
        if not config_path.exists():
            print(f"Config file not found at: {config_path}")
            print("Using default configuration...")
        config = load_config(config_path)
        
        # Setup logging
        log_level = "DEBUG" if args.verbose else config.get("logging", {}).get("level", "INFO")
        logger = setup_logging(log_level, output_dir / "music-claude.log")
        
        logger.info(f"Starting music-claude organizer")
        logger.info(f"Music directory: {music_dir}")
        logger.info(f"Output directory: {output_dir}")
        logger.info(f"LLM enabled: {not args.no_llm}")
        if not args.no_llm:
            logger.info(f"LLM model: {args.model}")
        
        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize album-level pipeline
        pipeline = AlbumMusicPipeline(
            config=config,
            enable_llm=not args.no_llm,
            output_dir=output_dir,
            model_name=args.model if not args.no_llm else None
        )
        
        # Process music library
        results = pipeline.process_library(
            music_dir=music_dir,
            limit=args.limit,
            execute=args.execute
        )
        
        logger.info(f"Processing complete. Processed {results['processed']} albums, "
                   f"skipped {results['skipped']}, failed {results['failed']}")
        
        if not args.execute:
            print(f"\nPlan generated. Review files in: {output_dir}")
            print("Run with --execute to perform the actual organization.")
        else:
            print(f"\nOrganization complete! Check {output_dir} for detailed logs.")
        
        return 0
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 1
    except MusicOrganizerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        # Handle encoding errors in the exception message itself
        try:
            error_msg = str(e)
        except (UnicodeDecodeError, UnicodeEncodeError):
            error_msg = repr(e).encode('utf-8', errors='replace').decode('utf-8')
        
        print(f"Unexpected error: {error_msg}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())