"""
Pipeline orchestrator that coordinates the four-stage music classification process.

This module manages the entire pipeline flow and provides the main interface
for processing music files through all stages.
"""

import csv
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from api.schemas import (
    RawFileInfo, ExtractedTrackInfo, EnrichedTrackInfo, 
    CanonicalTrackInfo, FinalTrackInfo, ProcessingResult, BatchProcessingResult
)
from api.client import ResilientAPIClient
from filesystem.file_ops import FileSystemOperations
from pipeline.stages import Stage1Triage, Stage2Extraction, Stage3Enrichment, Stage4Canonicalization
from caching.cache_manager import CacheManager
from utils.exceptions import MusicOrganizerError, FileProcessingError
from utils.config_loader import MusicConfig

logger = logging.getLogger(__name__)


class MusicPipeline:
    """
    Main pipeline orchestrator for music file classification and organization.
    """
    
    def __init__(
        self, 
        config: Dict[str, Any], 
        enable_llm: bool = True,
        output_dir: Path = None
    ):
        """
        Initialize the music processing pipeline.
        
        Args:
            config: Configuration dictionary
            enable_llm: Whether to enable LLM processing
            output_dir: Directory for output files
        """
        self.config = config
        self.enable_llm = enable_llm
        self.output_dir = output_dir or Path.cwd()
        
        # Initialize components
        self.filesystem_ops = FileSystemOperations(
            audio_extensions=config['filesystem']['audio_extensions'],
            ignored_dirs=config['filesystem']['ignored_dirs']
        )
        
        if enable_llm:
            self.api_client = ResilientAPIClient(
                max_retries=config['api']['max_retries'],
                timeout=config['api']['timeout_seconds'],
                api_cache_file=Path(config['caching']['api_cache_file']).expanduser(),
                cache_expiry_days=config['caching']['cache_expiry_days']
            )
        else:
            self.api_client = None
        
        self.cache_manager = CacheManager(
            execution_cache_file=Path(config['caching']['execution_cache_file']).expanduser(),
            api_cache_file=Path(config['caching']['api_cache_file']).expanduser(),
            expiry_days=config['caching']['cache_expiry_days']
        )
        
        # Initialize pipeline stages
        self.stage1 = Stage1Triage(self.filesystem_ops)
        
        if enable_llm:
            self.stage2 = Stage2Extraction(
                self.api_client, 
                config['api']['openai_model_extraction']
            )
            self.stage3 = Stage3Enrichment(
                self.api_client, 
                config['api']['openai_model_enrichment']
            )
        else:
            self.stage2 = None
            self.stage3 = None
            
        self.stage4 = Stage4Canonicalization()
        
        # Configuration for organization
        self.top_buckets = config['categories']['top_buckets']
        self.soundtrack_subs = config['categories']['soundtrack_subs']
        
        # Statistics
        self.stats = {
            'files_processed': 0,
            'files_skipped': 0,
            'files_failed': 0,
            'cache_hits': 0,
            'processing_time_total': 0.0
        }
    
    def process_library(
        self, 
        music_dir: Path, 
        limit: Optional[int] = None,
        execute: bool = False
    ) -> Dict[str, int]:
        """
        Process an entire music library.
        
        Args:
            music_dir: Root directory of the music library
            limit: Optional limit on number of files to process
            execute: Whether to execute the organization plan
            
        Returns:
            Dictionary with processing statistics
        """
        logger.info(f"Starting music library processing: {music_dir}")
        start_time = time.time()
        
        # Discover audio files
        logger.info("Discovering audio files...")
        audio_files = list(self.filesystem_ops.discover_audio_files(music_dir))
        
        if limit:
            audio_files = audio_files[:limit]
            logger.info(f"Limited processing to {limit} files")
        
        logger.info(f"Found {len(audio_files)} audio files to process")
        
        if not audio_files:
            logger.warning("No audio files found")
            return {'processed': 0, 'skipped': 0, 'failed': 0}
        
        # Process files
        if self.config['concurrency']['max_workers'] > 1:
            results = self._process_files_concurrent(audio_files)
        else:
            results = self._process_files_sequential(audio_files)
        
        # Generate outputs
        self._generate_output_files(results)
        
        # Execute organization if requested
        if execute:
            self._execute_organization_plan(results)
        
        processing_time = time.time() - start_time
        logger.info(f"Processing completed in {processing_time:.2f} seconds")
        
        # Return summary statistics
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful
        
        return {
            'processed': successful,
            'skipped': self.stats['files_skipped'],
            'failed': failed,
            'total_time': processing_time
        }
    
    def process_single_file(self, file_path: Path) -> ProcessingResult:
        """
        Process a single music file through all pipeline stages.
        
        Args:
            file_path: Path to the music file
            
        Returns:
            ProcessingResult with the outcome
        """
        start_time = time.time()
        
        try:
            logger.debug(f"Processing file: {file_path}")
            
            # Check cache first
            if self.cache_manager.is_file_cached(file_path):
                logger.debug(f"File found in cache, skipping: {file_path}")
                self.stats['cache_hits'] += 1
                return ProcessingResult(
                    original_path=file_path,
                    success=True,
                    final_info=None,  # Would load from cache in full implementation
                    error_message=None,
                    processing_time_seconds=time.time() - start_time,
                    pipeline_stage_completed="cached"
                )
            
            # Stage 1: Triage & Pre-Processing
            raw_info = self.stage1.process(file_path)
            if raw_info is None:
                logger.debug(f"File skipped in Stage 1: {file_path}")
                return ProcessingResult(
                    original_path=file_path,
                    success=False,
                    final_info=None,
                    error_message="File skipped (unsupported format or other reason)",
                    processing_time_seconds=time.time() - start_time,
                    pipeline_stage_completed="stage1"
                )
            
            if not self.enable_llm:
                # Fallback to heuristic processing
                return self._process_with_heuristics(raw_info, start_time)
            
            # Stage 2: Structured Data Extraction
            extracted_info = self.stage2.process(raw_info)
            
            # Stage 3: Semantic Enrichment
            enriched_info = self.stage3.process(extracted_info)
            
            # Stage 4: Canonicalization & Validation
            canonical_info = self.stage4.process(enriched_info)
            
            # Create final organized info
            final_info = self._create_final_info(canonical_info, raw_info)
            
            # Cache the result
            self.cache_manager.cache_file_result(file_path, final_info)
            
            processing_time = time.time() - start_time
            self.stats['files_processed'] += 1
            
            return ProcessingResult(
                original_path=file_path,
                success=True,
                final_info=final_info,
                error_message=None,
                processing_time_seconds=processing_time,
                pipeline_stage_completed="stage4"
            )
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to process {file_path}: {error_msg}")
            
            self.stats['files_failed'] += 1
            
            return ProcessingResult(
                original_path=file_path,
                success=False,
                final_info=None,
                error_message=error_msg,
                processing_time_seconds=time.time() - start_time,
                pipeline_stage_completed="error"
            )
    
    def _process_files_sequential(self, file_paths: List[Path]) -> List[ProcessingResult]:
        """Process files sequentially."""
        results = []
        
        for i, file_path in enumerate(file_paths):
            if i % 50 == 0 and i > 0:
                logger.info(f"Processed {i}/{len(file_paths)} files")
            
            result = self.process_single_file(file_path)
            results.append(result)
        
        return results
    
    def _process_files_concurrent(self, file_paths: List[Path]) -> List[ProcessingResult]:
        """Process files concurrently using ThreadPoolExecutor."""
        max_workers = self.config['concurrency']['max_workers']
        results = []
        
        logger.info(f"Processing {len(file_paths)} files with {max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_path = {
                executor.submit(self.process_single_file, file_path): file_path
                for file_path in file_paths
            }
            
            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_path):
                result = future.result()
                results.append(result)
                
                completed += 1
                if completed % 25 == 0:
                    logger.info(f"Completed {completed}/{len(file_paths)} files")
        
        return results
    
    def _process_with_heuristics(self, raw_info: RawFileInfo, start_time: float) -> ProcessingResult:
        """Process file using heuristics when LLM is disabled."""
        # Implement basic heuristic processing
        # This would extract basic info from filename and metadata
        
        # For now, create a simple final info
        final_info = FinalTrackInfo(
            track_number=None,
            artist=raw_info.filename.split(' - ')[0] if ' - ' in raw_info.filename else "Unknown Artist",
            title=raw_info.filename.split(' - ')[-1].rsplit('.', 1)[0] if ' - ' in raw_info.filename else raw_info.filename.rsplit('.', 1)[0],
            album=raw_info.existing_metadata.get('album'),
            year=None,
            genres=["Unknown"],
            moods=["Unknown"],
            instrumentation=["Unknown"],
            occasions=["General Listening"],
            energy_level=3,
            musicbrainz_id=None,
            canonical_artist=raw_info.filename.split(' - ')[0] if ' - ' in raw_info.filename else "Unknown Artist",
            canonical_album=raw_info.existing_metadata.get('album'),
            canonical_title=raw_info.filename.split(' - ')[-1].rsplit('.', 1)[0] if ' - ' in raw_info.filename else raw_info.filename.rsplit('.', 1)[0],
            official_release_year=None,
            confidence_score=0.5,
            format_tags=[],
            top_category="Library",
            sub_category=None,
            suggested_path=raw_info.file_path.parent / f"Library/{raw_info.filename}",
            organization_reason="Heuristic processing (LLM disabled)",
            processing_notes=["Processed with heuristics only"]
        )
        
        return ProcessingResult(
            original_path=raw_info.file_path,
            success=True,
            final_info=final_info,
            error_message=None,
            processing_time_seconds=time.time() - start_time,
            pipeline_stage_completed="heuristic"
        )
    
    def _create_final_info(self, canonical_info: CanonicalTrackInfo, raw_info: RawFileInfo) -> FinalTrackInfo:
        """Create final track info with organization details."""
        
        # Determine top category and sub-category
        top_category, sub_category = self._classify_track(canonical_info)
        
        # Generate suggested path
        suggested_path = self._generate_suggested_path(
            canonical_info, top_category, sub_category, raw_info.file_path
        )
        
        return FinalTrackInfo(
            **canonical_info.dict(),
            top_category=top_category,
            sub_category=sub_category,
            suggested_path=suggested_path,
            organization_reason=f"LLM classification with {canonical_info.confidence_score:.2f} confidence",
            processing_notes=[f"Processed through 4-stage pipeline"]
        )
    
    def _classify_track(self, canonical_info: CanonicalTrackInfo) -> tuple[str, Optional[str]]:
        """Classify track into top category and sub-category based on enriched info."""
        
        # Simple classification logic based on genres
        genres_lower = [g.lower() for g in canonical_info.genres]
        
        # Check for Classical
        if any(term in genres_lower for term in ['classical', 'symphony', 'concerto', 'opera', 'chamber']):
            return "Classical", None
        
        # Check for Electronic
        if any(term in genres_lower for term in ['electronic', 'techno', 'house', 'ambient', 'edm']):
            return "Electronic", None
        
        # Check for Jazz
        if any(term in genres_lower for term in ['jazz', 'blues', 'swing', 'bebop']):
            return "Jazz", None
        
        # Check for Soundtracks
        if any(term in genres_lower for term in ['soundtrack', 'film score', 'game music']):
            # Determine sub-category
            if any(term in genres_lower for term in ['film', 'movie']):
                return "Soundtracks", "Film"
            elif any(term in genres_lower for term in ['tv', 'television']):
                return "Soundtracks", "TV"
            elif any(term in genres_lower for term in ['game', 'video game']):
                return "Soundtracks", "Games"
            else:
                return "Soundtracks", "Film"  # Default
        
        # Default to Library for most music
        return "Library", None
    
    def _generate_suggested_path(
        self, 
        canonical_info: CanonicalTrackInfo, 
        top_category: str, 
        sub_category: Optional[str],
        original_path: Path
    ) -> Path:
        """Generate suggested organized file path."""
        
        # Start with music root (parent of original path's top-level directory)
        music_root = original_path.parents[len(original_path.parents) - 2]
        
        # Build path components
        path_parts = [top_category]
        
        if sub_category:
            path_parts.append(sub_category)
        
        # Add artist folder for relevant categories
        if top_category in ["Classical", "Library", "Electronic", "Jazz"]:
            artist_folder = self.filesystem_ops.sanitize_filename(canonical_info.canonical_artist)
            path_parts.append(artist_folder)
        
        # Create filename
        filename_parts = [canonical_info.canonical_title]
        if canonical_info.official_release_year:
            filename_parts.append(str(canonical_info.official_release_year))
        if canonical_info.format_tags:
            filename_parts.extend(canonical_info.format_tags)
        
        filename = " - ".join(filename_parts)
        filename = self.filesystem_ops.sanitize_filename(filename)
        filename += original_path.suffix
        
        return music_root / Path(*path_parts) / filename
    
    def _generate_output_files(self, results: List[ProcessingResult]):
        """Generate output files with processing results."""
        logger.info("Generating output files...")
        
        successful_results = [r for r in results if r.success and r.final_info]
        
        if not successful_results:
            logger.warning("No successful results to generate outputs for")
            return
        
        # Generate directory tree preview
        self._generate_directory_tree(successful_results)
        
        # Generate CSV plan
        self._generate_csv_plan(successful_results)
        
        # Generate processing summary
        self._generate_summary_report(results)
    
    def _generate_directory_tree(self, results: List[ProcessingResult]):
        """Generate a tree-like directory structure preview."""
        logger.info("Generating directory tree preview...")
        
        # Collect all suggested paths
        suggested_paths = []
        for result in results:
            if result.final_info and result.final_info.suggested_path:
                suggested_paths.append(result.final_info.suggested_path)
        
        if not suggested_paths:
            return
        
        # Build tree structure
        tree = self._build_directory_tree(suggested_paths)
        
        # Generate tree output
        tree_lines = self._format_directory_tree(tree)
        
        # Write to file
        tree_file = self.output_dir / "directory_structure_preview.txt"
        with open(tree_file, 'w', encoding='utf-8') as f:
            f.write("Proposed Directory Structure (directories only)\n")
            f.write("=" * 50 + "\n\n")
            f.write("\n".join(tree_lines))
        
        # Also print to console
        print("\n" + "=" * 60)
        print("PROPOSED DIRECTORY STRUCTURE")
        print("=" * 60)
        for line in tree_lines[:50]:  # Limit console output
            print(line)
        if len(tree_lines) > 50:
            print(f"... and {len(tree_lines) - 50} more directories")
            print(f"Full tree saved to: {tree_file}")
        print("=" * 60)
    
    def _build_directory_tree(self, paths: List[Path]) -> Dict[str, Any]:
        """Build a nested dictionary representing the directory tree."""
        tree = {}
        
        for path in paths:
            # Get only the directory part (not the file)
            dir_path = path.parent
            
            # Split into parts
            parts = dir_path.parts
            
            # Navigate/create the tree structure
            current_level = tree
            for part in parts:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
        
        return tree
    
    def _format_directory_tree(self, tree: Dict[str, Any], prefix: str = "", is_last: bool = True) -> List[str]:
        """Format the tree dictionary into tree-like ASCII art."""
        lines = []
        
        # Sort keys for consistent output
        sorted_keys = sorted(tree.keys())
        
        for i, key in enumerate(sorted_keys):
            is_last_item = (i == len(sorted_keys) - 1)
            
            # Choose the appropriate tree symbols
            if prefix == "":  # Root level
                current_prefix = ""
                symbol = ""
            else:
                symbol = "└── " if is_last_item else "├── "
                current_prefix = prefix
            
            # Add the current directory
            lines.append(f"{current_prefix}{symbol}{key}")
            
            # Recursively add subdirectories
            if tree[key]:  # If there are subdirectories
                extension = "    " if is_last_item else "│   "
                next_prefix = current_prefix + extension
                subtree_lines = self._format_directory_tree(tree[key], next_prefix, is_last_item)
                lines.extend(subtree_lines)
        
        return lines
    
    def _generate_csv_plan(self, results: List[ProcessingResult]):
        """Generate CSV file with the organization plan."""
        csv_file = self.output_dir / "organization_plan.csv"
        
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Original Path', 'Suggested Path', 'Artist', 'Title', 'Album', 
                'Year', 'Top Category', 'Sub Category', 'Confidence', 'Reason'
            ])
            
            for result in results:
                if result.final_info:
                    writer.writerow([
                        str(result.original_path),
                        str(result.final_info.suggested_path),
                        result.final_info.canonical_artist,
                        result.final_info.canonical_title,
                        result.final_info.canonical_album or '',
                        result.final_info.official_release_year or '',
                        result.final_info.top_category,
                        result.final_info.sub_category or '',
                        f"{result.final_info.confidence_score:.2f}",
                        result.final_info.organization_reason
                    ])
        
        logger.info(f"Organization plan saved to: {csv_file}")
    
    def _generate_summary_report(self, results: List[ProcessingResult]):
        """Generate a summary report of the processing."""
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        # Category breakdown
        category_counts = {}
        for result in successful:
            if result.final_info:
                cat = result.final_info.top_category
                if result.final_info.sub_category:
                    cat = f"{cat}/{result.final_info.sub_category}"
                category_counts[cat] = category_counts.get(cat, 0) + 1
        
        # Processing time stats
        processing_times = [r.processing_time_seconds for r in results]
        avg_time = sum(processing_times) / len(processing_times) if processing_times else 0
        
        summary = {
            'total_files': len(results),
            'successful': len(successful),
            'failed': len(failed),
            'success_rate': f"{(len(successful) / len(results) * 100):.1f}%" if results else "0%",
            'category_breakdown': category_counts,
            'average_processing_time': f"{avg_time:.2f}s",
            'total_processing_time': f"{sum(processing_times):.1f}s",
            'cache_statistics': self.cache_manager.get_cache_statistics() if hasattr(self, 'cache_manager') else {}
        }
        
        # Save to JSON
        summary_file = self.output_dir / "processing_summary.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        # Print summary to console
        print(f"\nPROCESSING SUMMARY")
        print(f"Total files: {summary['total_files']}")
        print(f"Successful: {summary['successful']}")
        print(f"Failed: {summary['failed']}")
        print(f"Success rate: {summary['success_rate']}")
        print(f"\nCategory breakdown:")
        for cat, count in sorted(category_counts.items()):
            print(f"  {cat}: {count} files")
        
        logger.info(f"Summary report saved to: {summary_file}")
    
    def _execute_organization_plan(self, results: List[ProcessingResult]):
        """Execute the organization plan by moving files."""
        logger.info("Executing organization plan...")
        
        successful_moves = 0
        for result in results:
            if result.success and result.final_info:
                try:
                    self.filesystem_ops.safe_move(
                        result.original_path,
                        result.final_info.suggested_path
                    )
                    successful_moves += 1
                except Exception as e:
                    logger.error(f"Failed to move {result.original_path}: {e}")
        
        logger.info(f"Successfully moved {successful_moves} files")
