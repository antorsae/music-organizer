"""
Simplified pipeline stages - Single LLM-powered processor with persona-driven logic.

This replaces the previous multi-stage approach with a single comprehensive
LLM call that uses a detailed persona prompt to perform all processing.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
from api.client import ResilientAPIClient
from api.schemas import AlbumInfo, FinalAlbumInfo, TrackNormalizationResult

logger = logging.getLogger(__name__)


class AlbumProcessorLLM:
    """
    Single-stage LLM processor that uses a comprehensive persona prompt 
    to extract, enrich, and canonicalize album information in one call.
    """
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str, include_tracklist: bool = False, normalize_tracks: bool = False):
        """
        Initialize the processor.
        
        Args:
            api_client: API client for LLM communication
            model_name: LLM model to use (e.g., "gpt-4o", "gpt-5")
            include_tracklist: Whether to include full tracklist in prompts for enhanced context
            normalize_tracks: Whether to include track filename normalization
        """
        self.api_client = api_client
        self.model_name = model_name
        self.include_tracklist = include_tracklist
        self.normalize_tracks = normalize_tracks
        self.system_prompt = self._load_system_prompt()
        
        # Initialize track normalization processor if needed
        if normalize_tracks:
            self.track_processor = TrackNormalizationProcessor(api_client, model_name)
        else:
            self.track_processor = None
    
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
        
        # Add track normalization if requested (separate LLM call)
        if self.normalize_tracks and self.track_processor:
            try:
                track_normalization = self.track_processor.process_tracks(album_info)
                # Create a new result with track normalization
                result_dict = result.model_dump()
                result_dict['track_normalization'] = track_normalization
                result = FinalAlbumInfo.model_validate(result_dict)
                logger.debug(f"Track normalization added: {len(track_normalization.track_renamings)} tracks")
            except Exception as e:
                logger.warning(f"Track normalization failed for {album_info.album_name}: {e}")
                # Continue without track normalization rather than failing the whole album
        
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


class TrackNormalizationProcessor:
    """
    Processor for normalizing track filenames within albums.
    Uses a specialized persona to analyze and clean track names.
    """
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        """
        Initialize the track normalization processor.
        
        Args:
            api_client: API client for LLM communication
            model_name: LLM model to use for track analysis
        """
        self.api_client = api_client
        self.model_name = model_name
        self.track_persona = self._load_track_persona()
    
    def _load_track_persona(self) -> str:
        """Load the track normalization persona system prompt."""
        persona_path = Path(__file__).parent / "track_persona.md"
        try:
            with open(persona_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error(f"Track persona file not found at {persona_path}")
            raise
        except Exception as e:
            logger.error(f"Error loading track persona file: {e}")
            raise
    
    def process_tracks(self, album_info: AlbumInfo) -> TrackNormalizationResult:
        """
        Process track filenames for normalization.
        
        Args:
            album_info: Album information containing track files
            
        Returns:
            TrackNormalizationResult with proposed renamings
        """
        logger.debug(f"Normalizing track names for: {album_info.album_name}")
        
        # Build prompt with track filenames
        user_prompt = self._build_track_prompt(album_info)
        
        # Make LLM call for track normalization
        result = self.api_client.get_structured_response(
            prompt=user_prompt,
            model=self.model_name,
            response_model=TrackNormalizationResult,
            system_prompt=self.track_persona,
            temperature=0.0,
            max_tokens=3000
        )
        
        logger.debug(f"Track normalization complete: {len(result.track_renamings)} tracks analyzed")
        
        return result
    
    def _build_track_prompt(self, album_info: AlbumInfo) -> str:
        """
        Build user prompt for track normalization.
        
        Args:
            album_info: Album information
            
        Returns:
            Formatted prompt for track analysis
        """
        audio_extensions = {'.flac', '.mp3', '.ogg', '.dsf', '.wav', '.aiff', '.ape', '.wv', '.m4a', '.opus'}
        
        # Filter only audio files
        audio_files = [
            filename for filename in album_info.track_files
            if any(filename.lower().endswith(ext) for ext in audio_extensions)
        ]
        
        # Format track listing
        track_listing = "\n".join([f"- {filename}" for filename in audio_files])
        
        return f"""
Analyze and normalize the track filenames in this album folder:

**Album Folder**: {album_info.album_name}

**Audio Files**:
{track_listing}

**Instructions**:
1. Find the longest common prefix (LCP) across ALL audio files
2. Determine the track numbering pattern (consistent/inconsistent/none)
3. For each audio file, propose a normalized filename following the NN. Track Title.extension format
4. Remove redundant prefixes, clean spacing, and apply title case
5. Flag any issues like duplicate track numbers or inconsistent patterns

Respond with the JSON object containing analysis and proposed renamings.
"""


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