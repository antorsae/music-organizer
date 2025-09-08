"""
Pydantic schemas for the music classification pipeline.

These models define the data structures passed between pipeline stages
and enforce schema validation on LLM responses.
"""

from typing import List, Optional, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field, validator


class AlbumInfo(BaseModel):
    """Album information after discovery and analysis."""
    
    album_path: Path = Field(..., description="Absolute path to the album directory")
    album_name: str = Field(..., description="Album directory name")
    parent_dirs: List[str] = Field(default_factory=list, description="Parent directory names")
    track_count: int = Field(..., description="Number of audio tracks")
    track_files: List[str] = Field(..., description="List of track filenames")
    track_paths: List[Path] = Field(..., description="List of track file paths")
    has_disc_structure: bool = Field(..., description="Whether album has disc subdirectories")
    disc_subdirs: List[str] = Field(default_factory=list, description="Disc subdirectory names if any")
    total_size_mb: float = Field(..., description="Total album size in MB")
    sample_metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata from sample tracks")
    
    class Config:
        arbitrary_types_allowed = True


class RawFileInfo(BaseModel):
    """Stage 1: Raw file information after triage and pre-processing."""
    
    file_path: Path = Field(..., description="Absolute path to the music file")
    filename: str = Field(..., description="Original filename")
    parent_dirs: List[str] = Field(default_factory=list, description="Parent directory names")
    existing_metadata: Dict[str, Any] = Field(default_factory=dict, description="Existing ID3/metadata tags")
    file_size_bytes: int = Field(..., description="File size in bytes")
    audio_format: str = Field(..., description="Audio file format (e.g., 'mp3', 'flac')")
    
    class Config:
        # Allow Path objects to be serialized
        arbitrary_types_allowed = True


class ExtractedTrackInfo(BaseModel):
    """Stage 2: Structured data extraction from filename and metadata."""
    
    track_number: Optional[int] = Field(
        default=None, 
        description="Track number extracted from filename or metadata (use null if not found)",
        ge=1, le=999
    )
    
    @validator('track_number', pre=True)
    def parse_track_number(cls, v):
        """Handle empty strings and convert to None."""
        if v is None or v == "" or v == "null":
            return None
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None
    
    artist: str = Field(
        ..., 
        description="Primary performing artist or band name",
        min_length=1, max_length=200
    )
    
    title: str = Field(
        ..., 
        description="Song title",
        min_length=1, max_length=200
    )
    
    album: Optional[str] = Field(
        default=None, 
        description="Album name if discernible",
        max_length=200
    )
    
    year: Optional[int] = Field(
        default=None,
        description="Release year if available (use null if not found)", 
        ge=1900, le=2030
    )
    
    @validator('year', pre=True)
    def parse_year(cls, v):
        """Handle empty strings and convert to None."""
        if v is None or v == "" or v == "null":
            return None
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None
    
    @validator('artist', 'title', 'album')
    def strip_whitespace(cls, v):
        """Strip leading/trailing whitespace from text fields."""
        return v.strip() if v else v


class EnrichedTrackInfo(ExtractedTrackInfo):
    """Stage 3: Semantic enrichment with genre, mood, and context."""
    
    genres: List[str] = Field(
        ..., 
        description="3-5 relevant genres and sub-genres",
        min_items=1, max_items=8
    )
    
    moods: List[str] = Field(
        ..., 
        description="3-5 descriptive moods that capture the track's feeling",
        min_items=1, max_items=8
    )
    
    instrumentation: List[str] = Field(
        ...,
        description="3-5 key instruments featured in the track",
        min_items=1, max_items=8
    )
    
    occasions: List[str] = Field(
        ...,
        description="2-3 suitable occasions or activities for this track",
        min_items=1, max_items=5
    )
    
    energy_level: int = Field(
        ...,
        description="Energy level from 1 (very calm) to 5 (very energetic)",
        ge=1, le=5
    )
    
    @validator('genres', 'moods', 'instrumentation', 'occasions')
    def validate_string_lists(cls, v):
        """Ensure list items are non-empty strings."""
        if not v:
            raise ValueError("List cannot be empty")
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("All list items must be non-empty strings")
        return [item.strip() for item in v]


class CanonicalTrackInfo(EnrichedTrackInfo):
    """Stage 4: Canonicalized and validated track information."""
    
    musicbrainz_id: Optional[str] = Field(
        default=None,
        description="MusicBrainz recording ID if found"
    )
    
    canonical_artist: str = Field(
        ...,
        description="Canonical artist name from music database"
    )
    
    canonical_album: Optional[str] = Field(
        default=None,
        description="Canonical album name from music database"
    )
    
    canonical_title: str = Field(
        ...,
        description="Canonical track title from music database"
    )
    
    official_release_year: Optional[int] = Field(
        default=None,
        description="Official release year from music database",
        ge=1900, le=2030
    )
    
    confidence_score: float = Field(
        ...,
        description="Confidence score for the database match",
        ge=0.0, le=1.0
    )
    
    format_tags: List[str] = Field(
        default_factory=list,
        description="Audio format tags (XRCD, K2HD, etc.)"
    )


class FinalTrackInfo(CanonicalTrackInfo):
    """Final processed track information with organization details."""
    
    top_category: str = Field(
        ...,
        description="Top-level category (Classical, Jazz, Library, etc.)"
    )
    
    sub_category: Optional[str] = Field(
        default=None,
        description="Sub-category for Soundtracks (Film, TV, Games, etc.)"
    )
    
    suggested_path: Path = Field(
        ...,
        description="Suggested organized file path"
    )
    
    organization_reason: str = Field(
        ...,
        description="Explanation for the organization decision"
    )
    
    processing_notes: List[str] = Field(
        default_factory=list,
        description="Notes about the processing pipeline"
    )
    
    class Config:
        arbitrary_types_allowed = True


class ExtractedAlbumInfo(BaseModel):
    """Stage 2: Structured album data extraction."""
    
    artist: str = Field(default="Unknown Artist", description="Primary album artist or band name (use 'Unknown Artist' if unable to determine)")
    album_title: str = Field(default="Unknown Album", description="Album title (use 'Unknown Album' if unable to determine)")
    year: Optional[int] = Field(default=None, description="Album release year (use null if not found)")
    total_tracks: int = Field(..., description="Total number of tracks in album")
    disc_count: Optional[int] = Field(default=1, description="Number of discs (1 if single disc)")
    
    @validator('year', pre=True)
    def parse_year(cls, v):
        """Handle empty strings and convert to None."""
        if v is None or v == "" or v == "null":
            return None
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None
    
    @validator('artist', 'album_title', pre=True)
    def handle_none_and_strip(cls, v):
        """Handle None values and strip whitespace from text fields."""
        if v is None or v == "":
            return None  # Will trigger the default value
        return v.strip() if isinstance(v, str) else v


class EnrichedAlbumInfo(ExtractedAlbumInfo):
    """Stage 3: Semantic enrichment for the album."""
    
    genres: List[str] = Field(..., description="3-5 relevant genres for this album")
    moods: List[str] = Field(..., description="3-5 descriptive moods for this album")
    style_tags: List[str] = Field(..., description="Style descriptors (e.g., 'symphonic', 'acoustic', 'electronic')")
    target_audience: List[str] = Field(..., description="Target audience/occasions (e.g., 'classical enthusiasts', 'workout', 'study')")
    energy_level: int = Field(..., description="Overall energy level 1-5", ge=1, le=5)
    is_compilation: bool = Field(..., description="Whether this is a compilation/various artists album")
    
    @validator('genres', 'moods', 'style_tags', 'target_audience')
    def validate_string_lists(cls, v):
        """Ensure list items are non-empty strings."""
        if not v:
            raise ValueError("List cannot be empty")
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("All list items must be non-empty strings")
        return [item.strip() for item in v]


class FinalAlbumInfo(EnrichedAlbumInfo):
    """Final processed album information with organization details."""
    
    canonical_artist: str = Field(..., description="Canonical artist name")
    canonical_album_title: str = Field(..., description="Canonical album title")
    musicbrainz_release_id: Optional[str] = Field(default=None, description="MusicBrainz release ID if found")
    
    top_category: str = Field(..., description="Top-level category")
    sub_category: Optional[str] = Field(default=None, description="Sub-category if applicable")
    
    suggested_album_dir: Path = Field(..., description="Suggested organized album directory path")
    organization_reason: str = Field(..., description="Explanation for the organization decision")
    
    confidence_score: float = Field(..., description="Confidence in classification", ge=0.0, le=1.0)
    format_tags: List[str] = Field(default_factory=list, description="Audio format tags")
    processing_notes: List[str] = Field(default_factory=list, description="Processing notes")
    
    class Config:
        arbitrary_types_allowed = True


class AlbumProcessingResult(BaseModel):
    """Result of processing a single album."""
    
    album_info: AlbumInfo
    success: bool
    final_album_info: Optional[FinalAlbumInfo] = None
    error_message: Optional[str] = None
    processing_time_seconds: float
    pipeline_stage_completed: str
    
    class Config:
        arbitrary_types_allowed = True


class ProcessingResult(BaseModel):
    """Result of processing a single music file."""
    
    original_path: Path
    success: bool
    final_info: Optional[FinalTrackInfo] = None
    error_message: Optional[str] = None
    processing_time_seconds: float
    pipeline_stage_completed: str  # Which stage was completed last
    
    class Config:
        arbitrary_types_allowed = True


class BatchProcessingResult(BaseModel):
    """Result of processing a batch of music files."""
    
    total_files: int
    processed_successfully: int
    failed_files: int
    skipped_files: int
    total_processing_time_seconds: float
    results: List[ProcessingResult]
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        if self.total_files == 0:
            return 0.0
        return (self.processed_successfully / self.total_files) * 100