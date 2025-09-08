"""
Multi-layer caching system for the music organizer.

Layer 1 (L1): Execution Cache - Prevents re-processing of already organized files
Layer 2 (L2): API Cache - Caches LLM API responses to avoid duplicate calls
Layer 3 (L3): Semantic Cache - Future enhancement for semantic similarity matching
"""

import json
import sqlite3
import hashlib
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from api.schemas import FinalAlbumInfo
from utils.exceptions import CacheError

logger = logging.getLogger(__name__)


class L1ExecutionCache:
    """
    Layer 1: Execution Cache using SQLite for persistent file processing state.
    """
    
    def __init__(self, cache_file: Path):
        self.cache_file = cache_file
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()
    
    def _init_database(self):
        """Initialize the SQLite database schema."""
        try:
            with sqlite3.connect(str(self.cache_file)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS processed_files (
                        file_path TEXT PRIMARY KEY,
                        file_size INTEGER,
                        last_modified REAL,
                        processed_timestamp REAL,
                        processing_result TEXT,
                        success BOOLEAN
                    )
                """)
                
                # Create index for faster lookups
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_processed_timestamp 
                    ON processed_files(processed_timestamp)
                """)
                
                conn.commit()
                logger.debug(f"Initialized execution cache database: {self.cache_file}")
                
        except sqlite3.Error as e:
            raise CacheError("execution", "initialization", str(e))
    
    def is_file_cached(self, file_path: Path) -> bool:
        """
        Check if a file has already been processed and is up to date.
        
        Args:
            file_path: Path to the file to check
            
        Returns:
            True if file is cached and current, False otherwise
        """
        try:
            file_stat = file_path.stat()
            file_size = file_stat.st_size
            last_modified = file_stat.st_mtime
            
            with sqlite3.connect(str(self.cache_file)) as conn:
                cursor = conn.execute("""
                    SELECT file_size, last_modified, success 
                    FROM processed_files 
                    WHERE file_path = ? AND success = 1
                """, (str(file_path),))
                
                result = cursor.fetchone()
                if result:
                    cached_size, cached_modified, success = result
                    # File is cached if size and modification time match
                    if cached_size == file_size and abs(cached_modified - last_modified) < 1.0:
                        logger.debug(f"File found in execution cache: {file_path}")
                        return True
                
                return False
                
        except (OSError, sqlite3.Error) as e:
            logger.warning(f"Error checking execution cache: {e}")
            return False
    
    def cache_file_result(self, file_path: Path, result: FinalAlbumInfo, success: bool = True):
        """
        Cache the processing result for a file.
        
        Args:
            file_path: Path to the processed file
            result: The processing result to cache
            success: Whether processing was successful
        """
        try:
            file_stat = file_path.stat()
            
            with sqlite3.connect(str(self.cache_file)) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO processed_files 
                    (file_path, file_size, last_modified, processed_timestamp, processing_result, success)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(file_path),
                    file_stat.st_size,
                    file_stat.st_mtime,
                    time.time(),
                    result.json() if result else None,
                    success
                ))
                conn.commit()
                
                logger.debug(f"Cached file result: {file_path}")
                
        except (OSError, sqlite3.Error) as e:
            logger.warning(f"Error caching file result: {e}")
    
    def get_cached_result(self, file_path: Path) -> Optional[FinalAlbumInfo]:
        """Get cached result for a file if available and current."""
        if not self.is_file_cached(file_path):
            return None
        
        try:
            with sqlite3.connect(str(self.cache_file)) as conn:
                cursor = conn.execute("""
                    SELECT processing_result 
                    FROM processed_files 
                    WHERE file_path = ? AND success = 1
                """, (str(file_path),))
                
                result = cursor.fetchone()
                if result and result[0]:
                    return FinalAlbumInfo.model_validate_json(result[0])
                
        except (sqlite3.Error, Exception) as e:
            logger.warning(f"Error retrieving cached result: {e}")
        
        return None
    
    def cleanup_old_entries(self, days_old: int = 30):
        """Remove cache entries older than specified days."""
        try:
            cutoff_time = time.time() - (days_old * 24 * 3600)
            
            with sqlite3.connect(str(self.cache_file)) as conn:
                cursor = conn.execute("""
                    DELETE FROM processed_files 
                    WHERE processed_timestamp < ?
                """, (cutoff_time,))
                
                deleted = cursor.rowcount
                conn.commit()
                
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old cache entries")
                
        except sqlite3.Error as e:
            logger.warning(f"Error cleaning up cache: {e}")


class L2APICache:
    """
    Layer 2: API Cache for caching LLM responses to avoid duplicate API calls.
    """
    
    def __init__(self, cache_file: Path, expiry_days: int = 30):
        self.cache_file = cache_file
        self.expiry_days = expiry_days
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache_data = self._load_cache()
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from file."""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    logger.debug(f"Loaded API cache with {len(cache_data)} entries")
                    return cache_data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading API cache: {e}")
        
        return {}
    
    def _save_cache(self):
        """Save cache to file."""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache_data, f, ensure_ascii=False, indent=2)
                logger.debug(f"Saved API cache with {len(self._cache_data)} entries")
        except IOError as e:
            logger.warning(f"Error saving API cache: {e}")
    
    def _generate_cache_key(self, prompt: str, model: str, **kwargs) -> str:
        """Generate a deterministic cache key for API requests."""
        # Create a hash of the request parameters
        key_data = {
            'prompt': prompt,
            'model': model,
            **{k: v for k, v in kwargs.items() if k in ['temperature', 'max_tokens', 'max_completion_tokens']}
        }
        
        key_str = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(key_str.encode('utf-8')).hexdigest()
    
    def get_cached_response(self, prompt: str, model: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Get cached API response if available and not expired.
        
        Args:
            prompt: The prompt sent to the API
            model: The model name
            **kwargs: Additional parameters that affect the response
            
        Returns:
            Cached response data or None if not found/expired
        """
        cache_key = self._generate_cache_key(prompt, model, **kwargs)
        
        if cache_key in self._cache_data:
            cached_entry = self._cache_data[cache_key]
            cached_time = cached_entry.get('timestamp', 0)
            
            # Check if cache entry is still valid
            if time.time() - cached_time < (self.expiry_days * 24 * 3600):
                logger.debug("API cache hit")
                return cached_entry.get('response')
            else:
                # Remove expired entry
                del self._cache_data[cache_key]
                logger.debug("API cache entry expired, removed")
        
        return None
    
    def cache_response(self, prompt: str, model: str, response: Dict[str, Any], **kwargs):
        """
        Cache an API response.
        
        Args:
            prompt: The prompt sent to the API
            model: The model name
            response: The response to cache
            **kwargs: Additional parameters
        """
        cache_key = self._generate_cache_key(prompt, model, **kwargs)
        
        self._cache_data[cache_key] = {
            'timestamp': time.time(),
            'response': response,
            'model': model
        }
        
        # Save cache periodically (every 10 new entries)
        if len(self._cache_data) % 10 == 0:
            self._save_cache()
        
        logger.debug("Cached API response")
    
    def cleanup_expired_entries(self):
        """Remove expired cache entries."""
        current_time = time.time()
        expiry_threshold = self.expiry_days * 24 * 3600
        
        expired_keys = []
        for key, entry in self._cache_data.items():
            if current_time - entry.get('timestamp', 0) > expiry_threshold:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self._cache_data[key]
        
        if expired_keys:
            self._save_cache()
            logger.info(f"Removed {len(expired_keys)} expired API cache entries")
    
    def force_save(self):
        """Force save the cache to disk."""
        self._save_cache()


class CacheManager:
    """
    Main cache manager that coordinates all caching layers.
    """
    
    def __init__(
        self, 
        execution_cache_file: Path, 
        api_cache_file: Path,
        expiry_days: int = 30
    ):
        """
        Initialize the cache manager.
        
        Args:
            execution_cache_file: Path to the execution cache SQLite file
            api_cache_file: Path to the API cache JSON file
            expiry_days: Number of days before cache entries expire
        """
        self.l1_cache = L1ExecutionCache(execution_cache_file)
        self.l2_cache = L2APICache(api_cache_file, expiry_days)
        
        # Statistics
        self.stats = {
            'l1_hits': 0,
            'l2_hits': 0,
            'total_requests': 0
        }
        
        logger.info("Cache manager initialized")
    
    def is_file_cached(self, file_path: Path) -> bool:
        """Check if file is in L1 execution cache."""
        self.stats['total_requests'] += 1
        
        if self.l1_cache.is_file_cached(file_path):
            self.stats['l1_hits'] += 1
            return True
        
        return False
    
    def cache_file_result(self, file_path: Path, result: FinalAlbumInfo):
        """Cache file processing result in L1 cache."""
        self.l1_cache.cache_file_result(file_path, result)
    
    def get_api_response(self, prompt: str, model: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Get cached API response from L2 cache."""
        response = self.l2_cache.get_cached_response(prompt, model, **kwargs)
        if response:
            self.stats['l2_hits'] += 1
        return response
    
    def cache_api_response(self, prompt: str, model: str, response: Dict[str, Any], **kwargs):
        """Cache API response in L2 cache."""
        self.l2_cache.cache_response(prompt, model, response, **kwargs)
    
    def cleanup_caches(self, days_old: int = None):
        """Clean up old entries from all cache layers."""
        days = days_old or 30
        
        logger.info("Cleaning up cache layers...")
        self.l1_cache.cleanup_old_entries(days)
        self.l2_cache.cleanup_expired_entries()
        logger.info("Cache cleanup completed")
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        """Get cache usage statistics."""
        l1_hit_rate = (
            self.stats['l1_hits'] / self.stats['total_requests'] * 100
            if self.stats['total_requests'] > 0 else 0
        )
        
        l2_hit_rate = (
            self.stats['l2_hits'] / self.stats['total_requests'] * 100
            if self.stats['total_requests'] > 0 else 0
        )
        
        return {
            'l1_execution_cache_hits': self.stats['l1_hits'],
            'l2_api_cache_hits': self.stats['l2_hits'],
            'total_requests': self.stats['total_requests'],
            'l1_hit_rate_percent': round(l1_hit_rate, 2),
            'l2_hit_rate_percent': round(l2_hit_rate, 2),
            'cache_files': {
                'execution_cache': str(self.l1_cache.cache_file),
                'api_cache': str(self.l2_cache.cache_file)
            }
        }
    
    def force_save_all(self):
        """Force save all caches to disk."""
        self.l2_cache.force_save()
        logger.debug("Forced save of all caches")