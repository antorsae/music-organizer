"""
Structured logging configuration for the music organizer.

This module sets up comprehensive logging with proper formatting, levels,
and output destinations for debugging and monitoring the application.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    max_file_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    console_output: bool = True
) -> logging.Logger:
    """
    Set up comprehensive logging configuration.
    
    Args:
        level: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        log_file: Path to log file (optional)
        max_file_size: Maximum log file size in bytes before rotation
        backup_count: Number of backup files to keep
        console_output: Whether to output logs to console
        
    Returns:
        Configured logger instance
    """
    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    # Set logging level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f'Invalid log level: {level}')
    
    root_logger.setLevel(numeric_level)
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # File handler with rotation
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_file),
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # Create main application logger
    app_logger = logging.getLogger('music-claude')
    app_logger.setLevel(numeric_level)
    
    # Log startup message
    app_logger.info(f"Logging initialized - Level: {level}")
    if log_file:
        app_logger.info(f"Log file: {log_file}")
    
    return app_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(f'music-claude.{name}')


class LoggerMixin:
    """
    Mixin class that provides easy access to a logger instance.
    
    Usage:
        class MyClass(LoggerMixin):
            def method(self):
                self.logger.info("This is a log message")
    """
    
    @property
    def logger(self) -> logging.Logger:
        """Get logger instance for this class."""
        class_name = self.__class__.__name__
        return get_logger(class_name)


def log_function_call(func):
    """
    Decorator to automatically log function entry and exit.
    
    Usage:
        @log_function_call
        def my_function(arg1, arg2):
            return result
    """
    import functools
    import time
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        
        # Log entry
        func_name = func.__name__
        logger.debug(f"Entering {func_name}")
        
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            
            # Log successful exit
            duration = time.time() - start_time
            logger.debug(f"Exiting {func_name} (took {duration:.3f}s)")
            
            return result
            
        except Exception as e:
            # Log exception
            duration = time.time() - start_time
            logger.error(f"Exception in {func_name} after {duration:.3f}s: {e}")
            raise
    
    return wrapper


def log_processing_progress(
    current: int, 
    total: int, 
    logger: logging.Logger,
    message_template: str = "Processed {current}/{total} items ({percentage:.1f}%)"
):
    """
    Log processing progress at appropriate intervals.
    
    Args:
        current: Current item count
        total: Total item count
        logger: Logger instance to use
        message_template: Template for progress message
    """
    if total == 0:
        return
    
    percentage = (current / total) * 100
    
    # Log at different intervals based on total size
    if total <= 100:
        # Log every 10% for small batches
        if current % max(1, total // 10) == 0 or current == total:
            logger.info(message_template.format(
                current=current, total=total, percentage=percentage
            ))
    elif total <= 1000:
        # Log every 5% for medium batches
        if current % max(1, total // 20) == 0 or current == total:
            logger.info(message_template.format(
                current=current, total=total, percentage=percentage
            ))
    else:
        # Log every 1% for large batches
        if current % max(1, total // 100) == 0 or current == total:
            logger.info(message_template.format(
                current=current, total=total, percentage=percentage
            ))


def configure_library_logging():
    """Configure logging for external libraries to reduce noise."""
    
    # Reduce noise from common libraries
    library_loggers = [
        'urllib3',
        'requests',
        'openai',
        'mutagen',
        'sqlite3'
    ]
    
    for lib_name in library_loggers:
        lib_logger = logging.getLogger(lib_name)
        lib_logger.setLevel(logging.WARNING)
    
    # Special handling for OpenAI client
    openai_logger = logging.getLogger('openai')
    openai_logger.setLevel(logging.WARNING)
    
    # Only show HTTP errors, not all requests
    http_logger = logging.getLogger('httpx')
    http_logger.setLevel(logging.WARNING)