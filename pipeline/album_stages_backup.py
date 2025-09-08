"""
Album-level pipeline stages for efficient music classification.

This module implements the four-stage pipeline at the album level rather than
individual file level, dramatically reducing API calls and improving consistency.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

from api.schemas import (
    AlbumInfo, ExtractedAlbumInfo, EnrichedAlbumInfo, FinalAlbumInfo
)
from api.client import ResilientAPIClient
from filesystem.file_ops import FileSystemOperations
from filesystem.album_detector import AlbumDetector
from utils.exceptions import (
    FileProcessingError, UnsupportedFormatError, MetadataExtractionError,
    CanonicalizationError, DatabaseError
)

logger = logging.getLogger(__name__)


class AlbumStage1Analysis:
    """Stage 1: Album Analysis & Metadata Sampling."""
    
    def __init__(self, filesystem_ops: FileSystemOperations, album_detector: AlbumDetector):
        self.filesystem_ops = filesystem_ops
        self.album_detector = album_detector
    
    def process(self, album_path: Path) -> Optional[AlbumInfo]:
        """
        Analyze an album directory and sample metadata.
        
        Args:
            album_path: Path to the album directory
            
        Returns:
            AlbumInfo object with analyzed album data
        """
        try:
            logger.debug(f"Album Stage 1: Analyzing {album_path}")
            
            # Get basic album structure
            album_structure = self.album_detector.analyze_album_structure(album_path)
            
            if album_structure['track_count'] == 0:
                logger.info(f"Skipping album with no tracks: {album_path}")
                return None
            
            # Sample metadata from a few tracks
            sample_metadata = self._sample_track_metadata(album_structure['track_paths'][:3])
            
            return AlbumInfo(
                album_path=album_structure['album_path'],
                album_name=album_structure['album_name'],
                parent_dirs=album_structure['parent_dirs'],
                track_count=album_structure['track_count'],
                track_files=album_structure['track_files'],
                track_paths=album_structure['track_paths'],
                has_disc_structure=album_structure['has_disc_structure'],
                disc_subdirs=album_structure['disc_subdirs'],
                total_size_mb=album_structure['total_size_mb'],
                sample_metadata=sample_metadata
            )
            
        except Exception as e:
            raise FileProcessingError(f"Album Stage 1 failed for {album_path}: {e}")
    
    def _sample_track_metadata(self, track_paths: List[Path]) -> Dict[str, Any]:
        """Sample metadata from a few tracks to get album-level info."""
        combined_metadata = {}
        
        for track_path in track_paths[:3]:  # Sample first 3 tracks
            try:
                metadata = self.filesystem_ops.extract_metadata(track_path)
                
                # Collect common fields
                for field in ['artist', 'albumartist', 'album', 'date', 'year', 'genre']:
                    if field in metadata and metadata[field]:
                        if field not in combined_metadata:
                            combined_metadata[field] = []
                        combined_metadata[field].append(metadata[field])
                
            except Exception as e:
                logger.debug(f"Could not extract metadata from {track_path}: {e}")
                continue
        
        # Consolidate repeated values
        consolidated = {}
        for field, values in combined_metadata.items():
            # Find most common value
            if values:
                most_common = max(set(values), key=values.count)
                consolidated[field] = most_common
        
        return consolidated


class AlbumStage2Extraction:
    """Stage 2: Album-Level Structured Data Extraction."""
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        self.api_client = api_client
        self.model_name = model_name
    
    def _sanitize_unicode(self, text: str) -> str:
        """Sanitize Unicode text to prevent encoding errors."""
        try:
            text.encode('utf-8')
            return text
        except UnicodeEncodeError:
            sanitized = []
            for char in text:
                try:
                    char.encode('utf-8')
                    sanitized.append(char)
                except UnicodeEncodeError:
                    sanitized.append('?')
            return ''.join(sanitized)
    
    def process(self, album_info: AlbumInfo) -> ExtractedAlbumInfo:
        """
        Extract structured album data using LLM.
        
        Args:
            album_info: Album information from Stage 1
            
        Returns:
            ExtractedAlbumInfo object
        """
        logger.debug(f"Album Stage 2: Extracting data for {album_info.album_name}")
        
        prompt = self._build_extraction_prompt(album_info)
        
        extracted_info = self.api_client.get_structured_response(
            prompt=prompt,
            model=self.model_name,
            response_model=ExtractedAlbumInfo,
            temperature=0.0
        )
        
        logger.debug(f"Album Stage 2: Extracted - Artist: {extracted_info.artist}, "
                    f"Album: {extracted_info.album_title}, Year: {extracted_info.year}")
        
        return extracted_info
    
    def _build_extraction_prompt(self, album_info: AlbumInfo) -> str:
        """Build the extraction prompt for album-level processing."""
        
        # Format existing metadata
        metadata_str = ""
        if album_info.sample_metadata:
            metadata_items = []
            for key, value in album_info.sample_metadata.items():
                if value and str(value).strip():
                    metadata_items.append(f"  {key}: {value}")
            if metadata_items:
                metadata_str = f"Sample track metadata:\n" + "\n".join(metadata_items)
        
        # Format track listing (show first 10 tracks)
        track_list = "\n".join([f"  {i+1:02d}. {track}" 
                               for i, track in enumerate(album_info.track_files[:10])])
        if len(album_info.track_files) > 10:
            track_list += f"\n  ... and {len(album_info.track_files) - 10} more tracks"
        
        # Parent directory context
        parent_path = " > ".join(album_info.parent_dirs) if album_info.parent_dirs else "None"
        
        return f"""
Extract album information from this music collection:

Album directory: {self._sanitize_unicode(album_info.album_name)}
Parent folders: {parent_path}
Total tracks: {album_info.track_count}
{f"Multi-disc album: {len(album_info.disc_subdirs)} discs" if album_info.has_disc_structure else "Single disc album"}

Track listing:
{track_list}

{metadata_str}

Extract and clean the following album information:
- artist: The primary album artist or band name (not "Various Artists" unless it's truly a compilation)
- album_title: The album title (remove format tags, clean up spacing)
- year: Album release year if found (4-digit number), or null if not found  
- total_tracks: Confirm the total number of tracks ({album_info.track_count})
- disc_count: Number of discs (1 for single disc, {len(album_info.disc_subdirs)} if multi-disc)

Clean up text: remove underscores, normalize spacing, proper capitalization.
Remove format indicators like [FLAC], (XRCD), etc. from album titles.
"""


class AlbumStage3Enrichment:
    """Stage 3: Album-Level Semantic Enrichment."""
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        self.api_client = api_client
        self.model_name = model_name
    
    def process(self, extracted_info: ExtractedAlbumInfo) -> EnrichedAlbumInfo:
        """
        Add semantic enrichment to album data.
        
        Args:
            extracted_info: Structured album data from Stage 2
            
        Returns:
            EnrichedAlbumInfo object
        """
        logger.debug(f"Album Stage 3: Enriching {extracted_info.artist} - {extracted_info.album_title}")
        
        prompt = self._build_enrichment_prompt(extracted_info)
        
        enriched_info = self.api_client.get_structured_response(
            prompt=prompt,
            model=self.model_name,
            response_model=EnrichedAlbumInfo,
            temperature=0.3
        )
        
        logger.debug(f"Album Stage 3: Enriched with {len(enriched_info.genres)} genres")
        
        return enriched_info
    
    def _build_enrichment_prompt(self, extracted_info: ExtractedAlbumInfo) -> str:
        """Build the enrichment prompt for album-level semantic analysis."""
        
        disc_info = f" ({extracted_info.disc_count} disc album)" if extracted_info.disc_count and extracted_info.disc_count > 1 else ""
        year_info = f" ({extracted_info.year})" if extracted_info.year else ""
        
        return f"""
Analyze this music album for classification and organization:

Artist: {extracted_info.artist}
Album: {extracted_info.album_title}
Year: {extracted_info.year or "Unknown"}
Tracks: {extracted_info.total_tracks}{disc_info}

Provide semantic analysis for this complete album:

1. Genres (3-5 specific genres):
   - Use specific genre names (e.g., "Symphonic Metal", "Cool Jazz", "Minimal Techno")
   - Include both broad and specific classifications
   - Consider the artist's typical style and this album's characteristics

2. Moods (3-5 descriptive moods):
   - Overall emotional character of the album
   - Use adjectives like "melancholic", "uplifting", "aggressive", "contemplative"

3. Style tags (3-5 descriptors):
   - Musical characteristics (e.g., "orchestral", "guitar-driven", "electronic", "acoustic")
   - Production style (e.g., "lo-fi", "polished", "live recording")

4. Target audience (2-3 categories):
   - Who would enjoy this album (e.g., "classical music enthusiasts", "electronic music fans")
   - Suitable occasions (e.g., "background study music", "workout", "late night listening")

5. Energy level (1-5 scale):
   - 1: Very calm/ambient (meditation, sleep)
   - 2: Relaxed (background, study)
   - 3: Moderate (casual listening)
   - 4: Energetic (active listening, light exercise)
   - 5: Very high energy (intense workout, party)

6. Is compilation:
   - true if this is various artists/compilation album
   - false if single artist/band album

Base your analysis on your knowledge of "{extracted_info.artist}" and the album "{extracted_info.album_title}"{year_info}.
"""


class AlbumStage4Canonicalization:
    """Stage 4: Album Canonicalization & Final Organization."""
    
    def __init__(self):
        pass
    
    def process(self, enriched_info: EnrichedAlbumInfo, album_info: AlbumInfo) -> FinalAlbumInfo:
        """
        Finalize album information and determine organization.
        
        Args:
            enriched_info: Enriched album data from Stage 3
            album_info: Original album info from Stage 1
            
        Returns:
            FinalAlbumInfo object with organization details
        """
        logger.debug(f"Album Stage 4: Finalizing {enriched_info.artist} - {enriched_info.album_title}")
        
        # Determine organization category
        top_category, sub_category = self._classify_album(enriched_info)
        
        # Generate suggested directory path
        suggested_dir = self._generate_album_path(enriched_info, album_info, top_category, sub_category)
        
        # Extract format tags from album name/folder
        format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
        
        return FinalAlbumInfo(
            **enriched_info.dict(),
            canonical_artist=self._canonicalize_artist(enriched_info.artist),
            canonical_album_title=self._canonicalize_title(enriched_info.album_title),
            musicbrainz_release_id=None,  # Would implement MusicBrainz lookup here
            top_category=top_category,
            sub_category=sub_category,
            suggested_album_dir=suggested_dir,
            organization_reason=f"Album-level classification: {top_category}" + (f"/{sub_category}" if sub_category else ""),
            confidence_score=0.85,  # Higher confidence for album-level processing
            format_tags=format_tags,
            processing_notes=[f"Processed as complete album ({enriched_info.total_tracks} tracks)"]
        )
    
    def _classify_album(self, enriched_info: EnrichedAlbumInfo) -> tuple[str, Optional[str]]:
        """Classify album into top category and sub-category."""
        
        genres_lower = [g.lower() for g in enriched_info.genres]
        
        # Check for Classical
        classical_indicators = [
            'classical', 'symphony', 'symphonic', 'concerto', 'opera', 'chamber', 
            'orchestral', 'baroque', 'romantic', 'modern classical'
        ]
        if any(term in ' '.join(genres_lower) for term in classical_indicators):
            return "Classical", None
        
        # Check for Electronic
        electronic_indicators = [
            'electronic', 'techno', 'house', 'ambient', 'edm', 'synth', 'electro'
        ]
        if any(term in ' '.join(genres_lower) for term in electronic_indicators):
            return "Electronic", None
        
        # Check for Jazz
        jazz_indicators = ['jazz', 'blues', 'swing', 'bebop', 'fusion', 'smooth jazz']
        if any(term in ' '.join(genres_lower) for term in jazz_indicators):
            return "Jazz", None
        
        # Check for Soundtracks
        soundtrack_indicators = ['soundtrack', 'score', 'film music', 'game music', 'ost']
        if any(term in ' '.join(genres_lower) for term in soundtrack_indicators):
            # Determine sub-category
            if any(term in ' '.join(genres_lower) for term in ['film', 'movie', 'cinema']):
                return "Soundtracks", "Film"
            elif any(term in ' '.join(genres_lower) for term in ['tv', 'television', 'series']):
                return "Soundtracks", "TV"
            elif any(term in ' '.join(genres_lower) for term in ['game', 'video game']):
                return "Soundtracks", "Games"
            elif any(term in ' '.join(genres_lower) for term in ['anime', 'ghibli']):
                return "Soundtracks", "Anime & Ghibli"
            elif any(term in ' '.join(genres_lower) for term in ['musical', 'broadway', 'stage']):
                return "Soundtracks", "Stage & Musicals"
            else:
                return "Soundtracks", "Film"  # Default
        
        # Check for Compilations
        if enriched_info.is_compilation:
            return "Compilations & VA", None
        
        # Default to Library for most music
        return "Library", None
    
    def _generate_album_path(self, enriched_info: EnrichedAlbumInfo, album_info: AlbumInfo, 
                            top_category: str, sub_category: Optional[str]) -> Path:
        """Generate the suggested organized album directory path."""
        
        # Start with music root (parent of album's current location)
        music_root = album_info.album_path.parents[len(album_info.parent_dirs)]
        
        # Build path components
        path_parts = [top_category]
        
        if sub_category:
            path_parts.append(sub_category)
        
        # Add artist folder for relevant categories
        if top_category in ["Classical", "Library", "Electronic", "Jazz"] and not enriched_info.is_compilation:
            artist_folder = self._sanitize_filename(enriched_info.artist)
            path_parts.append(artist_folder)
        
        # Create album folder name
        album_folder_parts = [enriched_info.album_title]
        if enriched_info.year:
            album_folder_parts.append(str(enriched_info.year))
        
        # Extract format tags from the album folder name
        format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
        if format_tags:
            album_folder_parts.extend(format_tags)
        
        album_folder = " - ".join(album_folder_parts)
        album_folder = self._sanitize_filename(album_folder)
        path_parts.append(album_folder)
        
        return music_root / Path(*path_parts)
    
    def _canonicalize_artist(self, artist: str) -> str:
        """Clean and normalize artist name."""
        artist = re.sub(r'\s+', ' ', artist.strip())
        return artist.title() if artist.islower() or artist.isupper() else artist
    
    def _canonicalize_title(self, title: str) -> str:
        """Clean and normalize album title."""
        # Remove common format indicators
        title = re.sub(r'\[(FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD)\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\(FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD\)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s+', ' ', title.strip())
        return title
    
    def _extract_format_tags(self, album_name: str, album_title: str) -> List[str]:
        """Extract format tags from album folder name or title."""
        text = f"{album_name} {album_title}"
        
        format_patterns = {
            'XRCD24': r'\bXRCD24\b',
            'XRCD': r'\bXRCD\b',
            'K2HD': r'\bK2HD\b',
            'SHM-CD': r'\bSHM-?CD\b',
            'MFSL': r'\b(MFSL|Mobile Fidelity)\b',
            'SACD': r'\bSACD\b',
            'DSD': r'\bDSD\b',
            '24-96': r'\b24[-/]96\b',
            '24-88': r'\b24[-/]88\b'
        }
        
        found_tags = []
        for tag, pattern in format_patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                found_tags.append(tag)
        
        return found_tags
    
    def _sanitize_filename(self, filename: str, max_length: int = 200) -> str:
        """Sanitize filename for cross-platform compatibility."""
        # Remove invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Remove control characters
        filename = ''.join(char for char in filename if ord(char) >= 32)
        
        # Normalize whitespace
        filename = ' '.join(filename.split())
        
        # Remove leading/trailing dots and spaces
        filename = filename.strip(' .')
        
        if not filename:
            filename = "unknown_album"
        
        # Truncate if too long
        if len(filename) > max_length:
            filename = filename[:max_length-4] + "..."
        
        return filename