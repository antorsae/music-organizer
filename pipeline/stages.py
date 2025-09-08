"""
Implementation of the four-stage music classification pipeline.

Stage 1: Triage & Pre-Processing
Stage 2: Structured Data Extraction  
Stage 3: Semantic Enrichment
Stage 4: Canonicalization & Validation
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

from api.schemas import (
    RawFileInfo, ExtractedTrackInfo, EnrichedTrackInfo, CanonicalTrackInfo
)
from api.client import ResilientAPIClient
from filesystem.file_ops import FileSystemOperations
from utils.exceptions import (
    FileProcessingError, UnsupportedFormatError, MetadataExtractionError,
    CanonicalizationError, DatabaseError
)

logger = logging.getLogger(__name__)


class Stage1Triage:
    """Stage 1: Triage & Pre-Processing - Validate files and extract existing metadata."""
    
    def __init__(self, filesystem_ops: FileSystemOperations):
        self.filesystem_ops = filesystem_ops
    
    def process(self, file_path: Path) -> Optional[RawFileInfo]:
        """
        Process a file through Stage 1: triage and pre-processing.
        
        Args:
            file_path: Path to the music file
            
        Returns:
            RawFileInfo object or None if file should be skipped
            
        Raises:
            FileProcessingError: If file processing fails
        """
        try:
            logger.debug(f"Stage 1: Processing {file_path}")
            
            # Validate audio format
            audio_format = self.filesystem_ops.validate_audio_format(file_path)
            
            # Get file info
            file_info = self.filesystem_ops.get_file_info(file_path)
            
            # Extract existing metadata
            existing_metadata = self.filesystem_ops.extract_metadata(file_path)
            
            # Build parent directory list
            parent_dirs = [p.name for p in file_path.parents][:-1]  # Exclude root
            parent_dirs.reverse()  # Order from root to parent
            
            return RawFileInfo(
                file_path=file_path,
                filename=file_path.name,
                parent_dirs=parent_dirs,
                existing_metadata=existing_metadata,
                file_size_bytes=file_info['size_bytes'],
                audio_format=audio_format
            )
            
        except UnsupportedFormatError:
            logger.info(f"Skipping unsupported format: {file_path}")
            return None
        except Exception as e:
            raise FileProcessingError(f"Stage 1 failed for {file_path}: {e}")


class Stage2Extraction:
    """Stage 2: Structured Data Extraction - Parse filename and metadata into clean structure."""
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        self.api_client = api_client
        self.model_name = model_name
    
    def process(self, raw_info: RawFileInfo) -> ExtractedTrackInfo:
        """
        Process raw file info through Stage 2: structured data extraction.
        
        Args:
            raw_info: Raw file information from Stage 1
            
        Returns:
            ExtractedTrackInfo object
        """
        logger.debug(f"Stage 2: Extracting structured data for {raw_info.filename}")
        
        # Build extraction prompt
        prompt = self._build_extraction_prompt(raw_info)
        
        # Get structured response from LLM
        extracted_info = self.api_client.get_structured_response(
            prompt=prompt,
            model=self.model_name,
            response_model=ExtractedTrackInfo,
            temperature=0.0
        )
        
        logger.debug(f"Stage 2: Extracted - Artist: {extracted_info.artist}, "
                    f"Title: {extracted_info.title}, Album: {extracted_info.album}")
        
        return extracted_info
    
    def _build_extraction_prompt(self, raw_info: RawFileInfo) -> str:
        """Build the extraction prompt for Stage 2."""
        
        # Format existing metadata for the prompt
        metadata_str = ""
        if raw_info.existing_metadata:
            metadata_items = []
            for key, value in raw_info.existing_metadata.items():
                if value and str(value).strip():
                    metadata_items.append(f"  {key}: {value}")
            if metadata_items:
                metadata_str = f"Existing metadata tags:\n" + "\n".join(metadata_items)
        
        # Format parent directories
        parent_path = " > ".join(raw_info.parent_dirs) if raw_info.parent_dirs else "None"
        
        return f"""
Extract music metadata from this file information:

Filename: {raw_info.filename}
Folder path: {parent_path}
Format: {raw_info.audio_format}

{metadata_str}

Extract and clean the following information:
- artist: The performing artist or band name
- title: The song title (remove file extensions, format tags like [FLAC])
- album: Album name if present in filename or metadata  
- track_number: Track number from filename (e.g. "01-Song.mp3" → 1), or null if not found
- year: Release year if found (4-digit number), or null if not found

Common patterns to handle:
- "Artist - Title.ext" 
- "01. Title.ext"
- "Artist - Album - Title.ext"
- "track_number-artist-title.ext"

Clean up: remove underscores, normalize spacing, proper capitalization.
"""


class Stage3Enrichment:
    """Stage 3: Semantic Enrichment - Infer genres, moods, and context."""
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        self.api_client = api_client
        self.model_name = model_name
    
    def process(self, extracted_info: ExtractedTrackInfo) -> EnrichedTrackInfo:
        """
        Process extracted info through Stage 3: semantic enrichment.
        
        Args:
            extracted_info: Structured data from Stage 2
            
        Returns:
            EnrichedTrackInfo object
        """
        logger.debug(f"Stage 3: Enriching {extracted_info.artist} - {extracted_info.title}")
        
        # Build enrichment prompt
        prompt = self._build_enrichment_prompt(extracted_info)
        
        # Get enriched response from LLM
        enriched_info = self.api_client.get_structured_response(
            prompt=prompt,
            model=self.model_name,
            response_model=EnrichedTrackInfo,
            temperature=0.3  # Allow some creativity for semantic inference
        )
        
        logger.debug(f"Stage 3: Enriched with {len(enriched_info.genres)} genres, "
                    f"{len(enriched_info.moods)} moods")
        
        return enriched_info
    
    def _build_enrichment_prompt(self, extracted_info: ExtractedTrackInfo) -> str:
        """Build the enrichment prompt for Stage 3."""
        
        album_info = f" from album '{extracted_info.album}'" if extracted_info.album else ""
        year_info = f" ({extracted_info.year})" if extracted_info.year else ""
        
        return f"""
You are a music expert with deep knowledge of genres, artists, and musical characteristics. Your task is to analyze and enrich the metadata for a music track with semantic information.

Track Information:
- Artist: {extracted_info.artist}
- Title: {extracted_info.title}
- Album: {extracted_info.album or "Unknown"}
- Year: {extracted_info.year or "Unknown"}
- Track Number: {extracted_info.track_number or "Unknown"}

Instructions:
1. Analyze "{extracted_info.artist} - {extracted_info.title}"{album_info}{year_info}

2. Suggest 3-5 relevant genres and sub-genres:
   - Be specific (e.g., "Indie Rock", "Neo-Soul", "Progressive House")
   - Include both broad and specific genres
   - Consider the time period and artist's typical style
   - Use established genre terminology

3. Identify 3-5 moods/emotions this track likely evokes:
   - Use descriptive adjectives (e.g., "melancholic", "energetic", "nostalgic", "uplifting")
   - Consider lyrical themes if you know the song
   - Think about the emotional impact

4. List 3-5 key instruments typically featured:
   - Include primary instruments (e.g., "electric guitar", "piano", "synthesizer")
   - Consider the genre and era
   - Be specific where possible

5. Suggest 2-3 occasions or activities this track suits:
   - Consider energy level and mood (e.g., "workout", "study", "party", "relaxation")
   - Think about typical use cases
   - Be practical and specific

6. Rate energy level from 1-5:
   - 1 = Very calm/ambient (sleep, meditation)
   - 2 = Relaxed (background, study)  
   - 3 = Moderate (casual listening)
   - 4 = Energetic (exercise, dancing)
   - 5 = Very high energy (intense workout, party)

Base your analysis on your knowledge of the artist and musical characteristics typical of their work.
"""


class Stage4Canonicalization:
    """Stage 4: Canonicalization & Validation - Fact-check against music databases."""
    
    def __init__(self):
        self.mb_client = None  # MusicBrainz client would be initialized here
    
    def process(self, enriched_info: EnrichedTrackInfo) -> CanonicalTrackInfo:
        """
        Process enriched info through Stage 4: canonicalization and validation.
        
        Args:
            enriched_info: Enriched data from Stage 3
            
        Returns:
            CanonicalTrackInfo object
        """
        logger.debug(f"Stage 4: Canonicalizing {enriched_info.artist} - {enriched_info.title}")
        
        # For now, implement a simplified version without external database queries
        # In a full implementation, this would query MusicBrainz, Discogs, etc.
        
        try:
            # Attempt to canonicalize with external database
            canonical_info = self._query_music_database(enriched_info)
            if canonical_info:
                return canonical_info
        except Exception as e:
            logger.warning(f"Database query failed: {e}")
        
        # Fallback: create canonical info from enriched info
        return self._create_fallback_canonical_info(enriched_info)
    
    def _query_music_database(self, enriched_info: EnrichedTrackInfo) -> Optional[CanonicalTrackInfo]:
        """
        Query external music database for canonical information.
        
        In a full implementation, this would use musicbrainzngs or similar library.
        """
        # Placeholder for database integration
        # This would implement the actual MusicBrainz/Discogs API calls
        
        # For demonstration, we'll simulate a database hit for well-known artists
        known_artists = {
            "the beatles": "The Beatles",
            "led zeppelin": "Led Zeppelin", 
            "pink floyd": "Pink Floyd",
            "radiohead": "Radiohead",
            "the rolling stones": "The Rolling Stones"
        }
        
        artist_lower = enriched_info.artist.lower()
        if artist_lower in known_artists:
            return CanonicalTrackInfo(
                **enriched_info.dict(),
                musicbrainz_id="example-mbid-12345",
                canonical_artist=known_artists[artist_lower],
                canonical_album=enriched_info.album,
                canonical_title=enriched_info.title,
                official_release_year=enriched_info.year,
                confidence_score=0.95,
                format_tags=[]
            )
        
        return None
    
    def _create_fallback_canonical_info(self, enriched_info: EnrichedTrackInfo) -> CanonicalTrackInfo:
        """Create canonical info when database lookup fails."""
        
        return CanonicalTrackInfo(
            **enriched_info.dict(),
            musicbrainz_id=None,
            canonical_artist=self._clean_artist_name(enriched_info.artist),
            canonical_album=enriched_info.album,
            canonical_title=self._clean_title(enriched_info.title),
            official_release_year=enriched_info.year,
            confidence_score=0.7,  # Lower confidence without database validation
            format_tags=self._extract_format_tags(enriched_info.title, enriched_info.album)
        )
    
    def _clean_artist_name(self, artist: str) -> str:
        """Clean and normalize artist name."""
        # Basic cleanup - in practice, this would be more sophisticated
        artist = re.sub(r'\s+', ' ', artist.strip())
        return artist.title() if artist.islower() or artist.isupper() else artist
    
    def _clean_title(self, title: str) -> str:
        """Clean and normalize track title."""
        # Remove common format indicators
        title = re.sub(r'\[(FLAC|MP3|WAV|ALAC)\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\(FLAC|MP3|WAV|ALAC\)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s+', ' ', title.strip())
        return title
    
    def _extract_format_tags(self, title: str, album: str) -> List[str]:
        """Extract format tags from title or album name."""
        text = f"{title} {album or ''}"
        
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