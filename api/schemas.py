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


# Track-level schemas removed in v2 - focusing on album-level processing


# Intermediate schemas removed in v2 - using single comprehensive LLM processing


class FinalAlbumInfo(BaseModel):
    """Final processed album information with organization details."""
    
    artist: str = Field(..., description="Canonical Artist Name")
    album_title: str = Field(..., description="Normalized Album Title")
    year: Optional[int] = Field(default=None, description="Album release year")
    top_category: str = Field(..., description="Top-level category (Classical, Jazz, Library, etc.)")
    sub_category: Optional[str] = Field(default=None, description="Sub-category for Soundtracks (Film, TV, Games, etc.)")
    final_path: str = Field(..., description="Complete suggested path for organization")
    format_tags: List[str] = Field(default_factory=list, description="Audio format tags (SACD, XRCD, etc.)")
    is_compilation: bool = Field(..., description="Whether this is a compilation/various artists album")
    confidence: float = Field(..., description="Confidence score for the classification", ge=0.0, le=1.0)
    
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
            return "Unknown"  # Fallback for required fields
        return v.strip() if isinstance(v, str) else v


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


# Track-level processing results removed in v2 - using album-level processing only