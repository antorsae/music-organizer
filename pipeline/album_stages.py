"""
Simplified pipeline stages - Single LLM-powered processor with persona-driven logic.

This replaces the previous multi-stage approach with a single comprehensive
LLM call that uses a detailed persona prompt to perform all processing.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
from api.client import ResilientAPIClient
from api.schemas import AlbumInfo, FinalAlbumInfo

logger = logging.getLogger(__name__)


class AlbumProcessorLLM:
    """
    Single-stage LLM processor that uses a comprehensive persona prompt 
    to extract, enrich, and canonicalize album information in one call.
    """
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        """
        Initialize the processor.
        
        Args:
            api_client: API client for LLM communication
            model_name: LLM model to use (e.g., "gpt-4o", "gpt-5")
        """
        self.api_client = api_client
        self.model_name = model_name
        self.system_prompt = self._load_system_prompt()
    
    def _load_system_prompt(self) -> str:
        """Load the comprehensive persona system prompt."""
        persona_path = Path(__file__).parent / "persona.md"
        try:
            with open(persona_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error(f"Persona file not found at {persona_path}")
            raise
        except Exception as e:
            logger.error(f"Error loading persona file: {e}")
            raise
    
    def process(self, album_info: AlbumInfo) -> FinalAlbumInfo:
        """
        Process album information using the persona-driven LLM approach.
        
        Args:
            album_info: Raw album information from stage 1
            
        Returns:
            Final processed album information
        """
        logger.info(f"Processing album: {album_info.album_name}")
        
        # Build comprehensive user prompt with all available data
        user_prompt = self._build_user_prompt(album_info)
        
        # Make single LLM call with system prompt and user data
        result = self.api_client.get_structured_response(
            prompt=user_prompt,
            model=self.model_name,
            response_model=FinalAlbumInfo,
            system_prompt=self.system_prompt,
            temperature=0.0,  # Deterministic for consistency
            max_tokens=2000   # Sufficient for comprehensive response
        )
        
        logger.info(f"Successfully processed album: {result.artist} - {result.album_title}")
        logger.debug(f"Classification: {result.top_category}/{result.sub_category or 'None'}")
        
        return result
    
    def _build_user_prompt(self, album_info: AlbumInfo) -> str:
        """
        Build a comprehensive user prompt containing all album data.
        
        Args:
            album_info: Album information to include in prompt
            
        Returns:
            Formatted prompt string
        """
        # Format parent directories as breadcrumb
        parent_path = " > ".join(album_info.parent_dirs) if album_info.parent_dirs else "/"
        
        # Format disc structure info
        disc_info = ""
        if album_info.has_disc_structure and album_info.disc_subdirs:
            disc_list = ", ".join(album_info.disc_subdirs)
            disc_info = f"\n- Disc Structure: {len(album_info.disc_subdirs)} discs ({disc_list})"
        
        # Extract key metadata samples
        metadata_summary = self._summarize_metadata(album_info.sample_metadata)
        
        # Format track listing (first 10 tracks for context)
        track_sample = album_info.track_files[:10]
        if len(album_info.track_files) > 10:
            track_sample.append(f"... and {len(album_info.track_files) - 10} more tracks")
        track_listing = "\n".join([f"  {i+1:02d}. {track}" for i, track in enumerate(track_sample)])
        
        return f"""
Please analyze and classify this music album:

## Album Information
- **Folder Name**: {album_info.album_name}
- **Parent Directory Path**: {parent_path}
- **Track Count**: {album_info.track_count} tracks
- **Total Size**: {album_info.total_size_mb:.1f} MB{disc_info}

## Track Listing
{track_listing}

## Metadata Sample
{metadata_summary}

## Analysis Required
Based on this information, determine the correct classification, normalization, and final organization path for this album. Apply all persona rules strictly, including:

1. Classification according to the decision tree (Soundtracks → Classical → Jazz → Electronic → Compilations & VA → Library)
2. Quality control gates for genre traps and artist-specific rules
3. Normalization of artist/album names (aliases, CJK translation, format tags)
4. Generation of the final directory path

Respond with the JSON object containing all required fields.
"""
    
    def _summarize_metadata(self, metadata: Dict[str, Any]) -> str:
        """
        Summarize metadata into a readable format.
        
        Args:
            metadata: Sample metadata dictionary
            
        Returns:
            Formatted metadata summary
        """
        if not metadata:
            return "No metadata available"
        
        summary_parts = []
        
        # Extract key fields
        key_fields = ['artist', 'album', 'title', 'date', 'year', 'genre', 'albumartist', 'composer']
        
        for field in key_fields:
            if field in metadata and metadata[field]:
                value = metadata[field]
                # Handle lists/arrays
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value[:3])  # First 3 items
                summary_parts.append(f"- {field.title()}: {value}")
        
        # Add other interesting fields
        other_fields = {}
        for key, value in metadata.items():
            if key.lower() not in key_fields and value and len(str(value)) < 100:
                other_fields[key] = value
        
        if other_fields:
            other_items = [f"{k}: {v}" for k, v in list(other_fields.items())[:5]]
            summary_parts.append(f"- Other: {', '.join(other_items)}")
        
        return "\n".join(summary_parts) if summary_parts else "No relevant metadata found"


class AlbumStage1Triage:
    """
    Stage 1: Triage and pre-processing (kept from original implementation).
    This stage handles filesystem analysis and metadata extraction.
    """
    
    def __init__(self):
        """Initialize the triage stage."""
        pass
    
    def process(self, album_info: AlbumInfo) -> AlbumInfo:
        """
        Process album information through triage stage.
        Currently just validates and passes through the album info.
        
        Args:
            album_info: Album information from discovery
            
        Returns:
            Validated album information
        """
        logger.debug(f"Triage processing: {album_info.album_name}")
        
        # Validation and basic checks
        if album_info.track_count == 0:
            logger.warning(f"Album has no tracks: {album_info.album_name}")
        
        if album_info.total_size_mb < 1.0:
            logger.warning(f"Album unusually small: {album_info.total_size_mb:.1f} MB")
        
        logger.debug(f"Triage complete: {album_info.track_count} tracks, {album_info.total_size_mb:.1f} MB")
        
        return album_info