#!/usr/bin/env python3
"""
Regression checker for v2 album-level classification using LLM persona.

Parses tests/regression_album_cases.txt lines of the form:
  Artist - Album => Expected

Then constructs synthetic AlbumInfo and runs the AlbumProcessorLLM
to verify classification against expected results. Uses caching to avoid
redundant API calls.

NOTE: This version requires API calls since all logic is now in the LLM persona.
"""
import re
import os
from pathlib import Path
from typing import Tuple, Optional

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "tests" / "regression_album_cases.txt"

import sys
sys.path.insert(0, str(ROOT))

from api.schemas import AlbumInfo
from api.client import ResilientAPIClient
from pipeline.album_stages import AlbumProcessorLLM
from utils.config_loader import load_config


def parse_case(line: str) -> Optional[Tuple[str, str, str]]:
    """Parse a test case line."""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    # Format: Artist - Album => Expected
    m = re.match(r"(.+?)\s+-\s+(.+?)\s*=>\s*(.+)$", line)
    if not m:
        return None
    artist, album, expected = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return artist, album, expected


def expected_tuple(expected: str) -> Tuple[str, Optional[str]]:
    """Convert expected string to top_category/sub_category tuple."""
    if '/' in expected:
        top, sub = expected.split('/', 1)
        return top.strip(), sub.strip()
    return expected.strip(), None


def classify_case(processor: AlbumProcessorLLM, artist: str, album: str) -> Tuple[str, Optional[str]]:
    """Classify a test case using the LLM processor."""
    # Build minimal AlbumInfo for testing
    fake_root = Path("/tmp/music_regression_root")
    artist_dir = artist
    album_dir = album
    album_path = fake_root / artist_dir / album_dir
    parent_dirs = [artist_dir]

    album_info = AlbumInfo(
        album_path=album_path,
        album_name=album_dir,
        parent_dirs=parent_dirs,
        track_count=10,
        track_files=[f"{i:02d}.track.flac" for i in range(1, 11)],
        track_paths=[album_path / f"{i:02d}.track.flac" for i in range(1, 11)],
        has_disc_structure=False,
        disc_subdirs=[],
        total_size_mb=500.0,
        sample_metadata={"artist": artist, "album": album}  # Add some metadata context
    )

    try:
        final_info = processor.process(album_info)
        return final_info.top_category, final_info.sub_category
    except Exception as e:
        print(f"Error processing {artist} - {album}: {e}")
        return "Error", None


def main() -> int:
    """Run regression tests."""
    # Check for API key
    if not os.getenv('OPENAI_API_KEY'):
        print("ERROR: OPENAI_API_KEY not set. Regression tests require API access.")
        return 1
    
    # Load configuration
    config = load_config()
    
    # Initialize API client and processor
    api_client = ResilientAPIClient(
        max_retries=config['api']['max_retries'],
        timeout=config['api']['timeout_seconds']
    )
    
    # Use a fast model for regression tests if available, otherwise use configured model
    model_name = "gpt-4o-mini"  # Cheaper and faster for regression tests
    processor = AlbumProcessorLLM(api_client, model_name)
    
    # Load test cases
    cases = []
    with open(CASES_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parsed = parse_case(line)
            if parsed:
                cases.append(parsed)

    total = len(cases)
    if total == 0:
        print("No test cases found.")
        return 0
    
    print(f"Running {total} regression tests with model {model_name}...")
    print("(Using LLM calls - results will be more accurate but slower)")
    
    passed = 0
    failures = []

    for i, (artist, album, expected) in enumerate(cases, 1):
        if i % 10 == 0:
            print(f"Progress: {i}/{total}")
            
        want_top, want_sub = expected_tuple(expected)
        got_top, got_sub = classify_case(processor, artist, album)
        
        ok = (got_top == want_top) and ((want_sub or None) == (got_sub or None))
        if ok:
            passed += 1
        else:
            got_str = got_top + (f"/{got_sub}" if got_sub else "")
            failures.append((artist, album, expected, got_str))

    print(f"\nRegression test results:")
    print(f"Checked {total} cases: {passed} passed, {len(failures)} failed.")
    print(f"Success rate: {passed/total*100:.1f}%")
    
    if failures:
        print(f"\n{len(failures)} Failures:")
        for a, al, exp, got in failures:
            print(f" - {a} - {al}: expected {exp}, got {got}")
        return 1
    
    print("All regression tests passed! ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())