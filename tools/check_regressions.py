#!/usr/bin/env python3
"""
Quick regression checker for album-level classification rules.

Parses tests/regression_album_cases.txt lines of the form:
  Artist - Album => Expected

Then constructs synthetic AlbumInfo + EnrichedAlbumInfo and runs
AlbumStage4Canonicalization to verify top_category[/sub_category].

This avoids LLM calls and full library scans, and runs fast.
"""
import re
from pathlib import Path
from typing import Tuple, Optional

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "tests" / "regression_album_cases.txt"

import sys
sys.path.insert(0, str(ROOT))

from api.schemas import AlbumInfo, EnrichedAlbumInfo
from pipeline.album_stages import AlbumStage4Canonicalization


def parse_case(line: str) -> Optional[Tuple[str, str, str]]:
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
    # Expected can be "Jazz" or "Soundtracks/Film" etc.
    if '/' in expected:
        top, sub = expected.split('/', 1)
        return top.strip(), sub.strip()
    return expected.strip(), None


def classify_case(artist: str, album: str) -> Tuple[str, Optional[str]]:
    # Build minimal AlbumInfo
    fake_root = Path("/tmp/music_regression_root")
    artist_dir = artist
    album_dir = album
    album_path = fake_root / artist_dir / album_dir
    # parent_dirs should lead back to fake_root when popped by Stage4
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
        sample_metadata={}
    )

    # Build EnrichedAlbumInfo with minimal fields
    enriched = EnrichedAlbumInfo(
        artist=artist,
        album_title=album,
        year=None,
        total_tracks=10,
        disc_count=1,
        genres=["Unknown"],
        moods=["Unknown"],
        style_tags=["Unknown"],
        target_audience=["General"],
        energy_level=3,
        is_compilation=False,
    )

    stage4 = AlbumStage4Canonicalization()
    final = stage4.process(enriched, album_info)
    return final.top_category, final.sub_category


def main() -> int:
    cases = []
    with open(CASES_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parsed = parse_case(line)
            if parsed:
                cases.append(parsed)

    total = len(cases)
    passed = 0
    failures = []

    for artist, album, expected in cases:
        want_top, want_sub = expected_tuple(expected)
        got_top, got_sub = classify_case(artist, album)
        ok = (got_top == want_top) and ((want_sub or None) == (got_sub or None))
        if ok:
            passed += 1
        else:
            failures.append((artist, album, expected, f"{got_top}" + (f"/{got_sub}" if got_sub else "")))

    print(f"Checked {total} cases: {passed} passed, {len(failures)} failed.")
    if failures:
        print("\nFailures:")
        for a, al, exp, got in failures:
            print(f" - {a} - {al}: expected {exp}, got {got}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
