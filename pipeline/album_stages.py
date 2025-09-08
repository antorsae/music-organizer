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
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str, include_tracklist: bool = False):
        """
        Initialize the processor.
        
        Args:
            api_client: API client for LLM communication
            model_name: LLM model to use (e.g., "gpt-4o", "gpt-5")
            include_tracklist: Whether to include full tracklist in prompts for enhanced context
        """
        self.api_client = api_client
        self.model_name = model_name
        self.include_tracklist = include_tracklist
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
        user_prompt = self._build_comprehensive_user_prompt(album_info, self.include_tracklist)
        
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
    
    def _build_comprehensive_user_prompt(self, album_info: AlbumInfo, include_tracklist: bool) -> str:
        """
        Build the comprehensive user prompt with all available context.
        
        Args:
            album_info: Album information to include in prompt
            include_tracklist: Whether to include full tracklist for enhanced context
            
        Returns:
            Formatted prompt string with comprehensive context
        """
        # 1. Get Parent Folder Context
        parent_folder = album_info.parent_dirs[-1] if album_info.parent_dirs else "N/A"

        # 2. Get Album Folder
        album_folder = album_info.album_name

        # 3. Get Track Filenames (conditionally)
        tracklist_str = "Not provided."
        # For simplicity, let's always include it for now if the flag is on.
        # A more advanced version could detect ambiguity.
        if include_tracklist and album_info.track_files:
            # Format track listing (show first 15 tracks)
            tracks_to_show = album_info.track_files[:15]
            track_lines = [f"- {track}" for track in tracks_to_show]
            tracklist_str = "\n".join(track_lines)
            if len(album_info.track_files) > 15:
                tracklist_str += f"\n- ... and {len(album_info.track_files) - 15} more tracks"

        # 4. Get Metadata Sample
        metadata_str = "Not available."
        if album_info.sample_metadata:
            metadata_items = [f"- {k}: {v}" for k, v in album_info.sample_metadata.items() if v]
            if metadata_items:
                metadata_str = "\n".join(metadata_items)

        # 5. Assemble the final prompt
        prompt = f"""
Analyze the following album using all available context to determine its correct organization.

### Input Data

**1. Parent Folder:**
{parent_folder}

**2. Album Folder:**
{album_folder}

**3. Track Filenames:**
{tracklist_str}

**4. Metadata Sample:**
{metadata_str}

### Analysis Required

Using the context priority rules (Parent Folder → Track Filenames → Album Folder → Metadata), determine:

1. **Artist Identification**: Use Parent Folder as the strongest signal for artist identity
2. **Album Classification**: Apply the decision tree (Soundtracks → Classical → Jazz → Electronic → Compilations & VA → Library → Unknown)
3. **Genre Trap Detection**: Use Track Filenames to resolve ambiguous album titles
4. **Normalization**: Apply all cleanup, alias resolution, and CJK translation rules
5. **Quality Gates**: Apply all artist-specific and category-specific quality control rules

**Critical**: For single-artist collections like "Greatest Hits", the Parent Folder definitively identifies the artist. Use Track Filenames to verify and resolve any ambiguity in generic album titles.

Respond with the JSON object containing all required fields.
"""
        return self._sanitize_unicode(prompt)  # Ensure we sanitize the final prompt
    
    def _sanitize_unicode(self, text: str) -> str:
        """
        Sanitize Unicode text to prevent encoding errors.
        
        Args:
            text: Input text that may contain problematic Unicode
            
        Returns:
            Sanitized text safe for UTF-8 encoding
        """
        try:
            # First, try to encode/decode to catch surrogate errors
            text.encode('utf-8')
            return text
        except UnicodeEncodeError:
            logger.debug("Found problematic Unicode characters, sanitizing...")
            
            # Replace or remove problematic characters
            sanitized_chars = []
            for char in text:
                try:
                    # Test if this character can be encoded
                    char.encode('utf-8')
                    sanitized_chars.append(char)
                except UnicodeEncodeError:
                    # Replace problematic characters with a safe alternative
                    sanitized_chars.append('?')
            
            sanitized_text = ''.join(sanitized_chars)
            logger.debug(f"Sanitized text: removed/replaced {len(text) - len([c for c in sanitized_chars if c != '?'])} problematic characters")
            
            return sanitized_text
    
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