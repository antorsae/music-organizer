"""
Album-level pipeline orchestrator for efficient music library processing.

This orchestrator processes albums rather than individual files, dramatically
reducing API calls and improving classification consistency.
"""

import csv
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from api.schemas import (
    AlbumInfo, ExtractedAlbumInfo, EnrichedAlbumInfo, 
    FinalAlbumInfo, AlbumProcessingResult
)
from api.client import ResilientAPIClient
from filesystem.file_ops import FileSystemOperations
from filesystem.album_detector import AlbumDetector
from pipeline.album_stages import AlbumStage1Triage, AlbumProcessorLLM
from caching.cache_manager import CacheManager
from utils.exceptions import MusicOrganizerError, FileProcessingError

logger = logging.getLogger(__name__)


class AlbumMusicPipeline:
    """
    Album-level music processing pipeline orchestrator.
    """
    
    def __init__(
        self, 
        config: Dict[str, Any], 
        enable_llm: bool = True,
        output_dir: Path = None,
        model_name: str = None,
        include_tracklist: bool = False
    ):
        """Initialize the album-level music processing pipeline."""
        self.config = config
        self.enable_llm = enable_llm
        self.output_dir = output_dir or Path.cwd()
        self.model_name = model_name or config['api'].get('openai_model_extraction', 'gpt-5')
        self.include_tracklist = include_tracklist
        
        # Initialize components
        self.filesystem_ops = FileSystemOperations(
            audio_extensions=config['filesystem']['audio_extensions'],
            ignored_dirs=config['filesystem']['ignored_dirs']
        )
        
        self.album_detector = AlbumDetector(
            audio_extensions=config['filesystem']['audio_extensions'],
            ignored_dirs=config['filesystem']['ignored_dirs']
        )
        
        if enable_llm:
            self.api_client = ResilientAPIClient(
                max_retries=config['api']['max_retries'],
                timeout=config['api']['timeout_seconds']
            )
        else:
            self.api_client = None
        
        self.cache_manager = CacheManager(
            execution_cache_file=Path(config['caching']['execution_cache_file']).expanduser(),
            api_cache_file=Path(config['caching']['api_cache_file']).expanduser(),
            expiry_days=config['caching']['cache_expiry_days']
        )
        
        # Initialize pipeline stages
        self.stage1 = AlbumStage1Triage()
        
        if enable_llm:
            self.album_processor = AlbumProcessorLLM(
                self.api_client, 
                self.model_name,
                self.include_tracklist
            )
        else:
            self.album_processor = None
        
        # Configuration for organization
        self.top_buckets = config['categories']['top_buckets']
        self.soundtrack_subs = config['categories']['soundtrack_subs']
        
        # Statistics
        self.stats = {
            'albums_processed': 0,
            'albums_skipped': 0,
            'albums_failed': 0,
            'total_tracks': 0,
            'cache_hits': 0,
            'api_calls_saved': 0,
            'processing_time_total': 0.0
        }
    
    def process_library(
        self, 
        music_dir: Path, 
        limit: Optional[int] = None,
        execute: bool = False
    ) -> Dict[str, int]:
        """
        Process an entire music library at the album level.
        
        Args:
            music_dir: Root directory of the music library
            limit: Optional limit on number of albums to process
            execute: Whether to execute the organization plan
            
        Returns:
            Dictionary with processing statistics
        """
        logger.info(f"Starting album-level music library processing: {music_dir}")
        start_time = time.time()
        
        # Discover albums
        logger.info("Discovering albums...")
        album_paths = self.album_detector.discover_albums(music_dir)
        
        if limit:
            album_paths = album_paths[:limit]
            logger.info(f"Limited processing to {limit} albums")
        
        logger.info(f"Found {len(album_paths)} albums to process")
        
        if not album_paths:
            logger.warning("No albums found")
            return {'processed': 0, 'skipped': 0, 'failed': 0}
        
        # Convert paths to AlbumInfo objects
        logger.info("Analyzing album structures...")
        albums = []
        for album_path in album_paths:
            try:
                album_info = self._create_album_info(album_path)
                albums.append(album_info)
            except Exception as e:
                logger.error(f"Failed to analyze album {album_path}: {e}")
        
        logger.info(f"Successfully analyzed {len(albums)} albums")
        
        # Process albums
        if self.config['concurrency']['max_workers'] > 1:
            results = self._process_albums_concurrent(albums)
        else:
            results = self._process_albums_sequential(albums)
        
        # Generate outputs
        self._generate_output_files(results)
        
        # Execute organization if requested
        if execute:
            self._execute_organization_plan(results)
        
        processing_time = time.time() - start_time
        logger.info(f"Album processing completed in {processing_time:.2f} seconds")
        
        # Calculate statistics
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful
        total_tracks = sum(r.album_info.track_count for r in results)
        
        # Estimate API calls saved
        api_calls_saved = total_tracks - len([r for r in results if r.success])
        
        print(f"\n🎵 ALBUM-LEVEL PROCESSING COMPLETE")
        print(f"Albums processed: {successful}")
        print(f"Albums failed: {failed}")
        print(f"Total tracks organized: {total_tracks}")
        print(f"API calls saved: ~{api_calls_saved} calls (vs file-level processing)")
        print(f"Processing time: {processing_time:.1f}s")
        
        return {
            'processed': successful,
            'skipped': self.stats['albums_skipped'],
            'failed': failed,
            'total_tracks': total_tracks,
            'api_calls_saved': api_calls_saved,
            'total_time': processing_time
        }
    
    def process_single_album(self, album_info: AlbumInfo) -> AlbumProcessingResult:
        """
        Process a single album through the simplified pipeline.
        
        Args:
            album_info: AlbumInfo from album discovery
            
        Returns:
            AlbumProcessingResult with the outcome
        """
        start_time = time.time()
        
        try:
            logger.debug(f"Processing album: {album_info.album_name}")
            
            # Stage 1: Triage (validation)
            album_info = self.stage1.process(album_info)
            
            # Check cache first (using album path as key)
            if self.cache_manager.is_file_cached(album_info.album_path):
                logger.debug(f"Album found in cache, skipping: {album_info.album_path}")
                self.stats['cache_hits'] += 1
                return AlbumProcessingResult(
                    album_info=album_info,
                    success=True,
                    final_album_info=None,  # Would load from cache in full implementation
                    error_message=None,
                    processing_time_seconds=time.time() - start_time,
                    pipeline_stage_completed="cached"
                )
            
            if not self.enable_llm:
                # Fallback to heuristic processing
                return self._process_album_with_heuristics(album_info, start_time)
            
            # Single LLM processing stage (replaces stages 2, 3, and 4)
            final_info = self.album_processor.process(album_info)
            
            # Cache the result
            # self.cache_manager.cache_file_result(album_info.album_path, final_info)  # Would need to adapt for albums
            
            processing_time = time.time() - start_time
            self.stats['albums_processed'] += 1
            self.stats['total_tracks'] += album_info.track_count
            
            return AlbumProcessingResult(
                album_info=album_info,
                success=True,
                final_album_info=final_info,
                error_message=None,
                processing_time_seconds=processing_time,
                pipeline_stage_completed="llm_complete"
            )
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to process album {album_info.album_name}: {error_msg}")
            
            self.stats['albums_failed'] += 1
            
            return AlbumProcessingResult(
                album_info=album_info if 'album_info' in locals() else None,
                success=False,
                final_album_info=None,
                error_message=error_msg,
                processing_time_seconds=time.time() - start_time,
                pipeline_stage_completed="error"
            )
    
    def _create_album_info(self, album_path: Path) -> AlbumInfo:
        """
        Create an AlbumInfo object from an album directory path.
        
        Args:
            album_path: Path to the album directory
            
        Returns:
            AlbumInfo object with filesystem analysis
        """
        # Get album tracks
        track_paths = self.album_detector.get_album_tracks(album_path)
        track_files = [track.name for track in track_paths]
        
        # Get parent directories (for context)
        parent_dirs = list(album_path.parents[0].parts) if album_path.parents else []
        
        # Analyze disc structure
        disc_subdirs = []
        has_disc_structure = False
        
        for subdir in album_path.iterdir():
            if (subdir.is_dir() and 
                self.album_detector.disc_dir_pattern.match(subdir.name)):
                disc_subdirs.append(subdir.name)
                has_disc_structure = True
        
        # Calculate total size
        total_size_bytes = sum(track.stat().st_size for track in track_paths if track.exists())
        total_size_mb = total_size_bytes / (1024 * 1024)
        
        # Sample metadata from first few tracks
        sample_metadata = {}
        if track_paths:
            # Try to extract metadata from the first track
            try:
                import mutagen
                first_track = track_paths[0]
                audio_file = mutagen.File(first_track)
                if audio_file:
                    # Convert mutagen metadata to simple dict
                    for key, value in audio_file.items():
                        if isinstance(value, list) and len(value) == 1:
                            sample_metadata[key] = value[0]
                        else:
                            sample_metadata[key] = value
            except Exception as e:
                logger.debug(f"Could not extract metadata from {first_track}: {e}")
        
        return AlbumInfo(
            album_path=album_path,
            album_name=album_path.name,
            parent_dirs=parent_dirs,
            track_count=len(track_paths),
            track_files=track_files,
            track_paths=track_paths,
            has_disc_structure=has_disc_structure,
            disc_subdirs=disc_subdirs,
            total_size_mb=total_size_mb,
            sample_metadata=sample_metadata
        )
    
    def _process_albums_sequential(self, albums: List[AlbumInfo]) -> List[AlbumProcessingResult]:
        """Process albums sequentially."""
        results = []
        
        for i, album_info in enumerate(albums):
            if i % 10 == 0 and i > 0:
                logger.info(f"Processed {i}/{len(albums)} albums")
            
            result = self.process_single_album(album_info)
            results.append(result)
        
        return results
    
    def _process_albums_concurrent(self, albums: List[AlbumInfo]) -> List[AlbumProcessingResult]:
        """Process albums concurrently using ThreadPoolExecutor."""
        max_workers = self.config['concurrency']['max_workers']
        results = []
        
        logger.info(f"Processing {len(albums)} albums with {max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_album = {
                executor.submit(self.process_single_album, album_info): album_info
                for album_info in albums
            }
            
            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_album):
                result = future.result()
                results.append(result)
                
                completed += 1
                if completed % 10 == 0:
                    logger.info(f"Completed {completed}/{len(albums)} albums")
        
        return results
    
    def _process_album_with_heuristics(self, album_info: AlbumInfo, start_time: float) -> AlbumProcessingResult:
        """Process album using heuristics when LLM is disabled."""
        
        # Simple heuristic classification based on folder names
        album_name_lower = album_info.album_name.lower()
        parent_dirs_lower = [p.lower() for p in album_info.parent_dirs]
        
        # Basic classification
        if any('classical' in p for p in parent_dirs_lower) or 'classical' in album_name_lower:
            top_category = "Classical"
        elif any('electronic' in p for p in parent_dirs_lower) or 'electronic' in album_name_lower:
            top_category = "Electronic"  
        elif any('jazz' in p for p in parent_dirs_lower) or 'jazz' in album_name_lower:
            top_category = "Jazz"
        elif any('soundtrack' in p for p in parent_dirs_lower) or 'ost' in album_name_lower:
            top_category = "Soundtracks"
        else:
            top_category = "Library"
        
        # Build final path
        artist = album_info.parent_dirs[-1] if album_info.parent_dirs else "Unknown Artist"
        final_path = f"{top_category}/{artist}/{album_info.album_name}"
        
        # Create simplified final info using new schema
        final_info = FinalAlbumInfo(
            artist=artist,
            album_title=album_info.album_name,
            year=None,
            top_category=top_category,
            sub_category=None,
            final_path=final_path,
            format_tags=[],
            is_compilation=False,
            confidence=0.5
        )
        
        return AlbumProcessingResult(
            album_info=album_info,
            success=True,
            final_album_info=final_info,
            error_message=None,
            processing_time_seconds=time.time() - start_time,
            pipeline_stage_completed="heuristic"
        )
    
    def _generate_output_files(self, results: List[AlbumProcessingResult]):
        """Generate output files with album processing results."""
        logger.info("Generating output files...")

        successful_results = [r for r in results if r.success and r.final_album_info]

        if not successful_results:
            logger.warning("No successful results to generate outputs for")
            return

        # Dedupe and normalize tags before rendering outputs
        successful_results = self._dedupe_album_results(successful_results)

        # Generate directory tree preview (by album)
        self._generate_album_directory_tree(successful_results)
        
        # Generate detailed CSV plan (by track)
        self._generate_detailed_track_plan(successful_results)
        
        # Generate album summary
        self._generate_album_summary(results)
        
        # Generate folder summary
        self._generate_folder_summary(results)
        
        # Generate comprehensive statistics
        self._generate_comprehensive_stats(results)
    
    def _generate_album_directory_tree(self, results: List[AlbumProcessingResult]):
        """Generate a tree-like directory structure preview for albums."""
        logger.info("Generating album directory tree preview...")
        
        # Collect all suggested album directories
        suggested_dirs = []
        for result in results:
            if result.final_album_info and result.final_album_info.final_path:
                # Convert final_path string to Path object
                suggested_dirs.append(Path(result.final_album_info.final_path))
        
        if not suggested_dirs:
            return
        
        # Build tree structure
        tree = self._build_directory_tree(suggested_dirs)
        
        # Generate tree output
        tree_lines = self._format_directory_tree(tree)
        
        # Write to file with error handling for invalid UTF-8 characters
        tree_file = self.output_dir / "album_directory_structure.txt"
        with open(tree_file, 'w', encoding='utf-8', errors='replace') as f:
            f.write("Proposed Album Directory Structure\n")
            f.write("=" * 50 + "\n\n")
            # Clean each line to handle invalid UTF-8 sequences
            for line in tree_lines:
                try:
                    f.write(line + "\n")
                except UnicodeEncodeError:
                    # Replace invalid characters with placeholder
                    clean_line = line.encode('utf-8', errors='replace').decode('utf-8')
                    f.write(clean_line + "\n")
        
        # Print to console with encoding error handling
        print("\n" + "=" * 60)
        print("PROPOSED ALBUM DIRECTORY STRUCTURE")
        print("=" * 60)
        for line in tree_lines[:40]:  # Show first 40 lines
            try:
                print(line)
            except UnicodeEncodeError:
                # Replace invalid characters for console output
                clean_line = line.encode('utf-8', errors='replace').decode('utf-8')
                print(clean_line)
        if len(tree_lines) > 40:
            print(f"... and {len(tree_lines) - 40} more directories")
            print(f"Full tree saved to: {tree_file}")
        print("=" * 60)
    
    def _generate_detailed_track_plan(self, results: List[AlbumProcessingResult]):
        """Generate detailed CSV with individual track mappings."""
        csv_file = self.output_dir / "track_organization_plan.csv"
        
        with open(csv_file, 'w', newline='', encoding='utf-8', errors='replace') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Original Track Path', 'Suggested Track Path', 'Album Artist', 'Album Title', 
                'Album Year', 'Top Category', 'Sub Category', 'Track Count', 'Confidence'
            ])
            
            for result in results:
                if result.final_album_info and result.album_info:
                    album_info = result.final_album_info
                    
                    # Write one row per track
                    for track_path in result.album_info.track_paths:
                        # Generate suggested track path
                        track_filename = track_path.name
                        suggested_album_dir = Path(album_info.final_path)
                        suggested_track_path = suggested_album_dir / track_filename
                        
                        # Clean strings to handle invalid UTF-8 sequences
                        def clean_str(s):
                            if isinstance(s, str):
                                return s.encode('utf-8', errors='replace').decode('utf-8')
                            return str(s)
                        
                        writer.writerow([
                            clean_str(track_path),
                            clean_str(suggested_track_path),
                            clean_str(album_info.artist),
                            clean_str(album_info.album_title),
                            album_info.year or '',
                            clean_str(album_info.top_category),
                            clean_str(album_info.sub_category) if album_info.sub_category else '',
                            result.album_info.track_count,
                            f"{album_info.confidence:.2f}"
                        ])
        
        logger.info(f"Detailed track plan saved to: {csv_file}")
    
    def _generate_album_summary(self, results: List[AlbumProcessingResult]):
        """Generate album processing summary."""
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        # Category breakdown
        category_counts = {}
        for result in successful:
            if result.final_album_info:
                cat = result.final_album_info.top_category
                if result.final_album_info.sub_category:
                    cat = f"{cat}/{result.final_album_info.sub_category}"
                category_counts[cat] = category_counts.get(cat, 0) + 1
        
        # Processing time stats
        processing_times = [r.processing_time_seconds for r in results]
        avg_time = sum(processing_times) / len(processing_times) if processing_times else 0
        total_tracks = sum(r.album_info.track_count for r in results if r.album_info)
        
        summary = {
            'total_albums': len(results),
            'successful_albums': len(successful),
            'failed_albums': len(failed),
            'total_tracks': total_tracks,
            'success_rate': f"{(len(successful) / len(results) * 100):.1f}%" if results else "0%",
            'category_breakdown': category_counts,
            'average_processing_time_per_album': f"{avg_time:.2f}s",
            'total_processing_time': f"{sum(processing_times):.1f}s",
            'estimated_api_calls_saved': total_tracks - len(successful) if self.enable_llm else 0,
            'efficiency_improvement': f"{((total_tracks - len(successful)) / total_tracks * 100):.1f}%" if total_tracks > 0 and self.enable_llm else "N/A"
        }
        
        # Save to JSON
        summary_file = self.output_dir / "album_processing_summary.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        # Print summary
        print(f"\nALBUM PROCESSING SUMMARY")
        print(f"Total albums: {summary['total_albums']}")
        print(f"Successful: {summary['successful_albums']}")
        print(f"Failed: {summary['failed_albums']}")
        print(f"Total tracks: {summary['total_tracks']}")
        print(f"Success rate: {summary['success_rate']}")
        if self.enable_llm:
            print(f"API calls saved: ~{summary['estimated_api_calls_saved']} ({summary['efficiency_improvement']} reduction)")
        print(f"\nAlbum categories:")
        for cat, count in sorted(category_counts.items()):
            print(f"  {cat}: {count} albums")
        
        logger.info(f"Album summary saved to: {summary_file}")

    # -------------------- De-duplication helpers --------------------
    def _canonical_album_key(self, info: FinalAlbumInfo) -> str:
        """Create a canonical key for an album for de-duplication."""
        tags = sorted(set(info.format_tags or []))
        year = info.year or ''
        title = (info.album_title or '').strip().lower()
        artist = (info.artist or '').strip().lower()
        return f"{artist}::{title}::{year}::{','.join(tags)}"

    def _dedupe_album_results(self, results: List[AlbumProcessingResult]) -> List[AlbumProcessingResult]:
        """Collapse duplicate albums and normalize tag lists."""
        seen = {}
        deduped: List[AlbumProcessingResult] = []
        for r in results:
            info = r.final_album_info
            if not info:
                continue
            # normalize tags once
            info.format_tags = sorted(set(info.format_tags or []))
            key = self._canonical_album_key(info)
            if key in seen:
                continue
            seen[key] = True
            deduped.append(r)
        return deduped
    
    def _build_directory_tree(self, paths: List[Path]) -> Dict[str, Any]:
        """Build a nested dictionary representing the directory tree."""
        tree = {}
        
        for path in paths:
            parts = path.parts
            current_level = tree
            for part in parts:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
        
        return tree
    
    def _format_directory_tree(self, tree: Dict[str, Any], prefix: str = "", is_last: bool = True) -> List[str]:
        """Format the tree dictionary into tree-like ASCII art."""
        lines = []
        sorted_keys = sorted(tree.keys())
        
        for i, key in enumerate(sorted_keys):
            is_last_item = (i == len(sorted_keys) - 1)
            
            if prefix == "":
                current_prefix = ""
                symbol = ""
            else:
                symbol = "└── " if is_last_item else "├── "
                current_prefix = prefix
            
            lines.append(f"{current_prefix}{symbol}{key}")
            
            if tree[key]:
                extension = "    " if is_last_item else "│   "
                next_prefix = current_prefix + extension
                subtree_lines = self._format_directory_tree(tree[key], next_prefix, is_last_item)
                lines.extend(subtree_lines)
        
        return lines
    
    def _execute_organization_plan(self, results: List[AlbumProcessingResult]):
        """Execute the organization plan by moving album directories."""
        logger.info("Executing album organization plan...")
        
        successful_moves = 0
        for result in results:
            if result.success and result.final_album_info and result.album_info:
                try:
                    source_dir = result.album_info.album_path
                    dest_dir = Path(result.final_album_info.final_path)
                    
                    # Create destination directory
                    dest_dir.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Move the entire album directory
                    source_dir.rename(dest_dir)
                    logger.info(f"Moved album: {source_dir} -> {dest_dir}")
                    successful_moves += 1
                    
                except Exception as e:
                    logger.error(f"Failed to move album {result.album_info.album_path}: {e}")
        
        logger.info(f"Successfully moved {successful_moves} albums")
    
    def _generate_folder_summary(self, results: List[AlbumProcessingResult]):
        """Generate summary of detected vs result music folders."""
        logger.info("Generating folder summary...")
        
        successful_results = [r for r in results if r.success and r.final_album_info]
        
        # Collect original and result folders
        original_folders = set()
        result_categories = {}
        result_folders = set()
        
        for result in results:
            if result.album_info:
                original_folders.add(str(result.album_info.album_path))
        
        for result in successful_results:
            final_info = result.final_album_info
            category = final_info.top_category
            if final_info.sub_category:
                category = f"{category}/{final_info.sub_category}"
            
            if category not in result_categories:
                result_categories[category] = []
            result_categories[category].append({
                'original': str(result.album_info.album_path.name),
                'result': str(Path(final_info.final_path).name),
                'artist': final_info.artist,
                'tracks': result.album_info.track_count
            })
            result_folders.add(str(final_info.final_path))
        
        # Write folder summary
        summary_file = self.output_dir / "folder_summary.txt"
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write("MUSIC LIBRARY FOLDER SUMMARY\n")
            f.write("=" * 50 + "\n\n")
            
            f.write(f"📁 DETECTED MUSIC FOLDERS: {len(original_folders)}\n")
            f.write("-" * 30 + "\n")
            for i, folder in enumerate(sorted(original_folders)[:20], 1):
                f.write(f"{i:3d}. {Path(folder).name}\n")
            if len(original_folders) > 20:
                f.write(f"     ... and {len(original_folders) - 20} more\n")
            
            f.write(f"\n🎯 RESULT ORGANIZATION: {len(result_categories)} categories\n")
            f.write("-" * 30 + "\n")
            for category, albums in result_categories.items():
                f.write(f"\n📂 {category} ({len(albums)} albums):\n")
                for album in sorted(albums, key=lambda x: x['artist'])[:10]:
                    tracks_info = f" ({album['tracks']} tracks)" if album['tracks'] > 0 else ""
                    f.write(f"   • {album['artist']} - {album['result']}{tracks_info}\n")
                if len(albums) > 10:
                    f.write(f"   ... and {len(albums) - 10} more albums\n")
        
        # Print summary to console
        print(f"\n📁 DETECTED MUSIC FOLDERS: {len(original_folders)}")
        print(f"🎯 ORGANIZED INTO: {len(result_categories)} categories")
        for category, albums in list(result_categories.items())[:5]:
            print(f"   📂 {category}: {len(albums)} albums")
        print(f"Full folder summary saved to: {summary_file}")
    
    def _generate_comprehensive_stats(self, results: List[AlbumProcessingResult]):
        """Generate comprehensive processing statistics."""
        logger.info("Generating comprehensive statistics...")
        
        # Calculate statistics
        total_albums = len(results)
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        # LLM call statistics
        llm_processed = [r for r in successful if r.pipeline_stage_completed not in ['heuristic', 'cached']]
        heuristic_processed = [r for r in successful if r.pipeline_stage_completed == 'heuristic']
        cached_processed = [r for r in successful if r.pipeline_stage_completed == 'cached']
        
        # Bundle analysis (albums from same artist/series)
        artist_bundles = {}
        for result in successful:
            if result.final_album_info:
                artist = result.final_album_info.artist
                if artist not in artist_bundles:
                    artist_bundles[artist] = []
                artist_bundles[artist].append(result)
        
        # Multi-album artists
        bundled_artists = {k: v for k, v in artist_bundles.items() if len(v) > 1}
        
        # Error analysis
        error_types = {}
        for result in failed:
            error = result.error_message or "Unknown error"
            error_type = error.split(':')[0] if ':' in error else error
            error_types[error_type] = error_types.get(error_type, 0) + 1
        
        # Track totals
        total_tracks = sum(r.album_info.track_count for r in results if r.album_info)
        successful_tracks = sum(r.album_info.track_count for r in successful if r.album_info)
        
        # Processing time analysis
        total_time = sum(r.processing_time_seconds for r in results)
        avg_time_per_album = total_time / total_albums if total_albums > 0 else 0
        
        # Write comprehensive stats
        stats_file = self.output_dir / "processing_statistics.txt"
        with open(stats_file, 'w', encoding='utf-8') as f:
            f.write("COMPREHENSIVE PROCESSING STATISTICS\n")
            f.write("=" * 50 + "\n\n")
            
            # Overview
            f.write("📊 PROCESSING OVERVIEW\n")
            f.write("-" * 25 + "\n")
            f.write(f"Total Albums Processed: {total_albums}\n")
            f.write(f"Successful: {len(successful)} ({len(successful)/total_albums*100:.1f}%)\n")
            f.write(f"Failed: {len(failed)} ({len(failed)/total_albums*100:.1f}%)\n")
            f.write(f"Total Tracks: {total_tracks:,}\n")
            f.write(f"Successful Tracks: {successful_tracks:,}\n")
            f.write(f"Total Processing Time: {total_time:.1f}s\n")
            f.write(f"Average Time per Album: {avg_time_per_album:.2f}s\n")
            
            # LLM Statistics
            f.write(f"\n🤖 LLM CALL STATISTICS\n")
            f.write("-" * 25 + "\n")
            f.write(f"LLM Processed: {len(llm_processed)} albums\n")
            f.write(f"Heuristic Processed: {len(heuristic_processed)} albums\n")
            f.write(f"Cached Results: {len(cached_processed)} albums\n")
            
            estimated_api_calls = len(llm_processed) * 3  # Typically 3 calls per album
            saved_calls = successful_tracks - estimated_api_calls if successful_tracks > estimated_api_calls else 0
            f.write(f"Estimated API Calls Made: {estimated_api_calls}\n")
            f.write(f"API Calls Saved vs File-Level: {saved_calls:,}\n")
            if successful_tracks > 0:
                efficiency = (saved_calls / successful_tracks) * 100
                f.write(f"Efficiency Improvement: {efficiency:.1f}%\n")
            
            # Bundle Analysis
            f.write(f"\n🎵 ALBUM BUNDLE ANALYSIS\n")
            f.write("-" * 25 + "\n")
            f.write(f"Total Artists: {len(artist_bundles)}\n")
            f.write(f"Multi-Album Artists: {len(bundled_artists)}\n")
            f.write(f"Single-Album Artists: {len(artist_bundles) - len(bundled_artists)}\n")
            
            if bundled_artists:
                f.write(f"\n🎼 TOP BUNDLED ARTISTS:\n")
                sorted_bundles = sorted(bundled_artists.items(), key=lambda x: len(x[1]), reverse=True)
                for artist, albums in sorted_bundles[:10]:
                    album_count = len(albums)
                    track_count = sum(a.album_info.track_count for a in albums if a.album_info)
                    f.write(f"   • {artist}: {album_count} albums, {track_count} tracks\n")
                    # Show album details
                    for album in albums[:3]:  # Show first 3 albums
                        if album.final_album_info:
                            f.write(f"     - {album.final_album_info.album_title}\n")
                    if len(albums) > 3:
                        f.write(f"     ... and {len(albums) - 3} more albums\n")
            
            # Error Analysis
            if error_types:
                f.write(f"\n❌ ERROR ANALYSIS\n")
                f.write("-" * 25 + "\n")
                for error_type, count in sorted(error_types.items(), key=lambda x: x[1], reverse=True):
                    f.write(f"   • {error_type}: {count} occurrences\n")
            
            # Performance Metrics
            f.write(f"\n⚡ PERFORMANCE METRICS\n")
            f.write("-" * 25 + "\n")
            if len(successful) > 0:
                successful_times = [r.processing_time_seconds for r in successful]
                f.write(f"Fastest Album: {min(successful_times):.2f}s\n")
                f.write(f"Slowest Album: {max(successful_times):.2f}s\n")
                f.write(f"Median Time: {sorted(successful_times)[len(successful_times)//2]:.2f}s\n")
            
            albums_per_minute = (len(successful) / (total_time / 60)) if total_time > 0 else 0
            f.write(f"Processing Rate: {albums_per_minute:.1f} albums/minute\n")
        
        # Print key stats to console  
        print(f"\n📊 PROCESSING STATISTICS")
        print(f"   Albums: {len(successful)}/{total_albums} successful ({len(successful)/total_albums*100:.1f}%)")
        print(f"   Tracks: {successful_tracks:,} organized")
        print(f"   LLM Calls: ~{len(llm_processed) * 3} (saved ~{saved_calls:,} vs file-level)")
        print(f"   Multi-Album Artists: {len(bundled_artists)}")
        if bundled_artists:
            top_artist = max(bundled_artists.items(), key=lambda x: len(x[1]))
            print(f"   Top Bundle: {top_artist[0]} ({len(top_artist[1])} albums)")
        print(f"Full statistics saved to: {stats_file}")
