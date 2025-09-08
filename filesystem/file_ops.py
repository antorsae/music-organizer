"""
Robust, cross-platform filesystem operations using pathlib.

This module provides safe, pathlib-based filesystem operations that work
consistently across different operating systems and handle various edge cases.
"""

import shutil
import stat
from pathlib import Path
from typing import List, Set, Iterator, Optional, Dict, Any
import logging
import mutagen
from mutagen.id3 import ID3NoHeaderError

from utils.exceptions import (
    FilesystemError, UnsupportedFormatError, MetadataExtractionError
)

logger = logging.getLogger(__name__)


class FileSystemOperations:
    """Handles all filesystem operations with proper error handling."""
    
    def __init__(self, audio_extensions: List[str], ignored_dirs: List[str]):
        """
        Initialize filesystem operations.
        
        Args:
            audio_extensions: List of supported audio file extensions (with dots)
            ignored_dirs: List of directory names to ignore during scanning
        """
        self.audio_extensions = {ext.lower() for ext in audio_extensions}
        self.ignored_dirs = {name.lower() for name in ignored_dirs}
    
    def discover_audio_files(self, root_dir: Path, recursive: bool = True) -> Iterator[Path]:
        """
        Discover audio files in a directory tree.
        
        Args:
            root_dir: Root directory to scan
            recursive: Whether to scan subdirectories recursively
            
        Yields:
            Path objects for discovered audio files
            
        Raises:
            FilesystemError: If the root directory cannot be accessed
        """
        if not root_dir.exists():
            raise FilesystemError(str(root_dir), "scan", "Directory does not exist")
        
        if not root_dir.is_dir():
            raise FilesystemError(str(root_dir), "scan", "Path is not a directory")
        
        try:
            pattern = "**/*" if recursive else "*"
            for path in root_dir.glob(pattern):
                if not path.is_file():
                    continue
                
                # Check if parent directory should be ignored
                if self._should_ignore_parent(path):
                    continue
                
                # Check file extension
                if path.suffix.lower() in self.audio_extensions:
                    yield path
                    
        except PermissionError as e:
            raise FilesystemError(str(root_dir), "scan", f"Permission denied: {e}")
        except OSError as e:
            raise FilesystemError(str(root_dir), "scan", f"OS error: {e}")
    
    def _should_ignore_parent(self, file_path: Path) -> bool:
        """Check if any parent directory should be ignored."""
        for parent in file_path.parents:
            if parent.name.lower() in self.ignored_dirs:
                return True
        return False
    
    def extract_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from an audio file.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            Dictionary containing extracted metadata
            
        Raises:
            MetadataExtractionError: If metadata extraction fails
        """
        try:
            # Check if file exists and is readable
            if not file_path.exists():
                logger.warning(f"File does not exist: {file_path}")
                return {}
            
            if not file_path.is_file():
                logger.warning(f"Path is not a file: {file_path}")
                return {}
            
            # Try to load with mutagen
            try:
                audio_file = mutagen.File(str(file_path))
            except Exception as load_error:
                logger.warning(f"Mutagen failed to load {file_path}: {load_error}")
                return {}  # Return empty metadata instead of failing
            
            if audio_file is None:
                # File format not recognized by mutagen, but that's OK
                logger.debug(f"Format not recognized by mutagen: {file_path}")
                return {}
            
            # Convert mutagen tags to a standard dictionary format
            metadata = {}
            
            # Common tags across formats
            tag_mapping = {
                'title': ['TIT2', 'TITLE', '\xa9nam'],
                'artist': ['TPE1', 'ARTIST', '\xa9ART'],
                'albumartist': ['TPE2', 'ALBUMARTIST', 'aART'],
                'album': ['TALB', 'ALBUM', '\xa9alb'],
                'date': ['TDRC', 'DATE', '\xa9day'],
                'year': ['TYER', 'YEAR'],
                'track': ['TRCK', 'TRACKNUMBER', 'trkn'],
                'genre': ['TCON', 'GENRE', '\xa9gen'],
            }
            
            for standard_key, possible_keys in tag_mapping.items():
                for key in possible_keys:
                    try:
                        if key in audio_file:
                            try:
                                value = audio_file[key]
                                if isinstance(value, list) and value:
                                    metadata[standard_key] = str(value[0])
                                elif value:
                                    metadata[standard_key] = str(value)
                                break
                            except Exception as tag_error:
                                logger.debug(f"Error reading tag {key} from {file_path}: {tag_error}")
                                continue
                    except (ValueError, KeyError, TypeError) as key_error:
                        # Handle cases where checking key existence fails
                        logger.debug(f"Error checking key {key} in {file_path}: {key_error}")
                        continue
            
            # Add file format info
            try:
                if hasattr(audio_file, 'info') and audio_file.info:
                    info = audio_file.info
                    metadata.update({
                        'length_seconds': getattr(info, 'length', 0),
                        'bitrate': getattr(info, 'bitrate', 0),
                        'sample_rate': getattr(info, 'sample_rate', 0),
                    })
            except Exception as info_error:
                logger.debug(f"Error reading file info from {file_path}: {info_error}")
            
            return metadata
            
        except ID3NoHeaderError:
            # File has no ID3 tags, return empty metadata (this is normal for FLAC)
            logger.debug(f"No ID3 tags found in {file_path} (this is normal for FLAC)")
            return {}
        except PermissionError as e:
            logger.error(f"Permission denied accessing {file_path}: {e}")
            return {}  # Don't fail the entire process for permission issues
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Unexpected error extracting metadata from {file_path}: {e}")
            logger.debug(f"Full traceback: {error_details}")
            return {}  # Return empty metadata instead of failing
    
    def get_file_info(self, file_path: Path) -> Dict[str, Any]:
        """
        Get basic file information.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary with file information
            
        Raises:
            FilesystemError: If file cannot be accessed
        """
        try:
            stat_result = file_path.stat()
            
            return {
                'size_bytes': stat_result.st_size,
                'modified_time': stat_result.st_mtime,
                'created_time': stat_result.st_ctime,
                'permissions': stat.filemode(stat_result.st_mode),
                'is_readonly': not (stat_result.st_mode & stat.S_IWRITE),
            }
            
        except OSError as e:
            raise FilesystemError(str(file_path), "stat", str(e))
    
    def safe_move(self, source: Path, destination: Path, create_dirs: bool = True) -> bool:
        """
        Safely move a file to a new location.
        
        Args:
            source: Source file path
            destination: Destination file path
            create_dirs: Whether to create parent directories if they don't exist
            
        Returns:
            True if the move was successful
            
        Raises:
            FilesystemError: If the move operation fails
        """
        try:
            # Resolve paths to absolute
            source = source.resolve()
            destination = destination.resolve()
            
            # Check source exists
            if not source.exists():
                raise FilesystemError(str(source), "move", "Source file does not exist")
            
            # Create parent directories if needed
            if create_dirs:
                destination.parent.mkdir(parents=True, exist_ok=True)
            
            # Check if destination already exists
            if destination.exists():
                if self._files_are_identical(source, destination):
                    # Files are identical, just remove source
                    source.unlink()
                    logger.info(f"Removed duplicate file: {source}")
                    return True
                else:
                    # Generate unique destination name
                    destination = self._generate_unique_path(destination)
                    logger.warning(f"Destination exists, using: {destination}")
            
            # Perform the move
            shutil.move(str(source), str(destination))
            logger.info(f"Moved file: {source} -> {destination}")
            return True
            
        except PermissionError as e:
            raise FilesystemError(str(source), "move", f"Permission denied: {e}")
        except OSError as e:
            raise FilesystemError(str(source), "move", f"OS error: {e}")
    
    def safe_copy(self, source: Path, destination: Path, create_dirs: bool = True) -> bool:
        """
        Safely copy a file to a new location.
        
        Args:
            source: Source file path
            destination: Destination file path
            create_dirs: Whether to create parent directories
            
        Returns:
            True if copy was successful
            
        Raises:
            FilesystemError: If the copy operation fails
        """
        try:
            source = source.resolve()
            destination = destination.resolve()
            
            if not source.exists():
                raise FilesystemError(str(source), "copy", "Source file does not exist")
            
            if create_dirs:
                destination.parent.mkdir(parents=True, exist_ok=True)
            
            if destination.exists():
                if self._files_are_identical(source, destination):
                    logger.info(f"File already exists and is identical: {destination}")
                    return True
                else:
                    destination = self._generate_unique_path(destination)
            
            shutil.copy2(str(source), str(destination))
            logger.info(f"Copied file: {source} -> {destination}")
            return True
            
        except PermissionError as e:
            raise FilesystemError(str(source), "copy", f"Permission denied: {e}")
        except OSError as e:
            raise FilesystemError(str(source), "copy", f"OS error: {e}")
    
    def _files_are_identical(self, path1: Path, path2: Path) -> bool:
        """Check if two files are identical by comparing size and modification time."""
        try:
            stat1 = path1.stat()
            stat2 = path2.stat()
            
            # Quick check: different sizes means different files
            if stat1.st_size != stat2.st_size:
                return False
            
            # For same-size files, compare modification times
            # (This is faster than full content comparison for large files)
            return abs(stat1.st_mtime - stat2.st_mtime) < 1.0
            
        except OSError:
            return False
    
    def _generate_unique_path(self, path: Path) -> Path:
        """Generate a unique file path by appending a number."""
        counter = 1
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        
        while True:
            new_name = f"{stem} ({counter}){suffix}"
            new_path = parent / new_name
            if not new_path.exists():
                return new_path
            counter += 1
            
            # Safety check to avoid infinite loops
            if counter > 1000:
                raise FilesystemError(str(path), "unique_path", "Too many duplicates")
    
    def sanitize_unicode_text(self, text: str) -> str:
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
            
            return ''.join(sanitized_chars)
    
    def sanitize_filename(self, filename: str, max_length: int = 200) -> str:
        """
        Sanitize a filename for cross-platform compatibility.
        
        Args:
            filename: Original filename
            max_length: Maximum length for the filename
            
        Returns:
            Sanitized filename safe for all operating systems
        """
        # First sanitize Unicode characters
        filename = self.sanitize_unicode_text(filename)
        
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Remove control characters
        filename = ''.join(char for char in filename if ord(char) >= 32)
        
        # Normalize whitespace
        filename = ' '.join(filename.split())
        
        # Remove leading/trailing dots and spaces (problematic on Windows)
        filename = filename.strip(' .')
        
        # Ensure not empty
        if not filename:
            filename = "unnamed_file"
        
        # Truncate if too long, but preserve extension
        if len(filename) > max_length:
            name_part = filename[:max_length-4]  # Leave room for extension
            if '.' in filename:
                ext_part = filename.split('.')[-1]
                filename = f"{name_part}.{ext_part}"
            else:
                filename = name_part
        
        return filename
    
    def validate_audio_format(self, file_path: Path) -> str:
        """
        Validate that a file is a supported audio format.
        
        Args:
            file_path: Path to the file to validate
            
        Returns:
            The detected audio format (file extension without dot)
            
        Raises:
            UnsupportedFormatError: If format is not supported
        """
        extension = file_path.suffix.lower()
        
        if extension not in self.audio_extensions:
            raise UnsupportedFormatError(str(file_path), extension)
        
        # Try to open with mutagen to verify it's actually an audio file
        try:
            audio_file = mutagen.File(str(file_path))
            if audio_file is None:
                raise UnsupportedFormatError(
                    str(file_path), 
                    f"{extension} (not a valid audio file)"
                )
        except Exception:
            raise UnsupportedFormatError(
                str(file_path),
                f"{extension} (corrupted or unreadable)"
            )
        
        return extension[1:]  # Return without the dot