"""
Album detection logic for identifying album directories in a music library.

This module identifies which directories represent albums vs auxiliary folders,
following the same logic as the original music_reorg_llm script.
"""

import re
from pathlib import Path
from typing import List, Set, Optional
import logging

logger = logging.getLogger(__name__)


class AlbumDetector:
    """Detects album directories in a music library."""
    
    def __init__(self, audio_extensions: List[str], ignored_dirs: List[str]):
        self.audio_extensions = {ext.lower() for ext in audio_extensions}
        self.ignored_dirs = {name.lower() for name in ignored_dirs}
        
        # Disc directory pattern (CD1, Disc 2, etc.)
        self.disc_dir_pattern = re.compile(r"(?i)^\s*(?:cd|disc|disk)[\s._-]*([0-9ivx]+)\s*$")
    
    def discover_albums(self, root_dir: Path) -> List[Path]:
        """
        Discover all album directories in the music library.
        
        Args:
            root_dir: Root directory to scan
            
        Returns:
            List of album directory paths
        """
        albums = []
        
        for path in sorted(root_dir.rglob("*")):
            if self._is_album_directory(path):
                albums.append(path)
                logger.debug(f"Found album: {path}")
        
        logger.info(f"Discovered {len(albums)} album directories")
        return albums
    
    def _is_album_directory(self, path: Path) -> bool:
        """
        Check if a directory is an album directory.
        
        Args:
            path: Directory path to check
            
        Returns:
            True if it's an album directory
        """
        if not path.is_dir():
            return False
        
        # Skip system directories
        if path.name.lower().startswith('@eadir'):
            return False
        
        # Skip ignored directories
        if path.name.lower() in self.ignored_dirs:
            return False
        
        # Skip pure disc directories (CD1, Disc 2, etc.)
        if self.disc_dir_pattern.match(path.name):
            return False
        
        # Must contain audio files (either directly or in disc subdirs)
        return self._has_audio_files(path)
    
    def _has_audio_files(self, path: Path) -> bool:
        """
        Check if directory has audio files (directly or in disc subdirs).
        
        Args:
            path: Directory to check
            
        Returns:
            True if audio files are found
        """
        try:
            # Check for audio files directly in the directory
            for file in path.iterdir():
                if file.is_file() and file.suffix.lower() in self.audio_extensions:
                    return True
            
            # Check for audio files in immediate disc subdirectories
            for subdir in path.iterdir():
                if (subdir.is_dir() and 
                    self.disc_dir_pattern.match(subdir.name)):
                    
                    for file in subdir.iterdir():
                        if file.is_file() and file.suffix.lower() in self.audio_extensions:
                            return True
            
        except (PermissionError, OSError) as e:
            logger.warning(f"Cannot access directory {path}: {e}")
            return False
        
        return False
    
    def get_album_tracks(self, album_dir: Path) -> List[Path]:
        """
        Get all audio files in an album directory.
        
        Args:
            album_dir: Album directory path
            
        Returns:
            List of audio file paths
        """
        tracks = []
        
        try:
            # Get files directly in the album directory
            for file in sorted(album_dir.iterdir()):
                if file.is_file() and file.suffix.lower() in self.audio_extensions:
                    tracks.append(file)
            
            # Get files from disc subdirectories
            for subdir in sorted(album_dir.iterdir()):
                if (subdir.is_dir() and 
                    self.disc_dir_pattern.match(subdir.name)):
                    
                    disc_tracks = []
                    for file in sorted(subdir.iterdir()):
                        if file.is_file() and file.suffix.lower() in self.audio_extensions:
                            disc_tracks.append(file)
                    
                    tracks.extend(disc_tracks)
            
        except (PermissionError, OSError) as e:
            logger.error(f"Error accessing album directory {album_dir}: {e}")
        
        return tracks
    
    def analyze_album_structure(self, album_dir: Path) -> dict:
        """
        Analyze the structure of an album directory.
        
        Args:
            album_dir: Album directory to analyze
            
        Returns:
            Dictionary with album structure information
        """
        tracks = self.get_album_tracks(album_dir)
        
        # Detect disc structure
        disc_subdirs = []
        for subdir in album_dir.iterdir():
            if (subdir.is_dir() and 
                self.disc_dir_pattern.match(subdir.name)):
                disc_subdirs.append(subdir.name)
        
        # Get parent directory names for context
        parent_dirs = [p.name for p in album_dir.parents][:-1]
        parent_dirs.reverse()
        
        return {
            'album_path': album_dir,
            'album_name': album_dir.name,
            'parent_dirs': parent_dirs,
            'track_count': len(tracks),
            'track_files': [t.name for t in tracks],
            'track_paths': tracks,
            'has_disc_structure': len(disc_subdirs) > 0,
            'disc_subdirs': sorted(disc_subdirs),
            'total_size_mb': sum(t.stat().st_size for t in tracks) / (1024 * 1024) if tracks else 0
        }