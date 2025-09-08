"""
Custom exception hierarchy for the music organizer application.

This module defines a structured hierarchy of exceptions that allows for
precise error handling and clear separation of different failure modes.
"""


class MusicOrganizerError(Exception):
    """Base class for all application-specific errors."""
    pass


class ConfigurationError(MusicOrganizerError):
    """Raised when there are configuration-related issues."""
    pass


class FileProcessingError(MusicOrganizerError):
    """Base class for errors during file validation or pre-processing."""
    pass


class UnsupportedFormatError(FileProcessingError):
    """Raised when a file is not a supported audio format."""
    
    def __init__(self, file_path: str, format_detected: str = None):
        self.file_path = file_path
        self.format_detected = format_detected
        
        if format_detected:
            message = f"Unsupported audio format '{format_detected}' for file: {file_path}"
        else:
            message = f"Could not determine audio format for file: {file_path}"
        
        super().__init__(message)


class MetadataExtractionError(FileProcessingError):
    """Raised when metadata cannot be extracted from an audio file."""
    
    def __init__(self, file_path: str, reason: str = None):
        self.file_path = file_path
        self.reason = reason
        
        message = f"Failed to extract metadata from file: {file_path}"
        if reason:
            message += f" - {reason}"
        
        super().__init__(message)


class MetadataPipelineError(MusicOrganizerError):
    """Base class for errors during the LLM pipeline."""
    pass


class APICommunicationError(MetadataPipelineError):
    """Raised for network or API status issues."""
    
    def __init__(self, message: str, status_code: int = None, retry_after: int = None):
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


class APIRateLimitError(APICommunicationError):
    """Raised when API rate limits are exceeded."""
    
    def __init__(self, retry_after: int = None):
        message = "API rate limit exceeded"
        if retry_after:
            message += f". Retry after {retry_after} seconds"
        super().__init__(message, status_code=429, retry_after=retry_after)


class APITimeoutError(APICommunicationError):
    """Raised when API requests timeout."""
    
    def __init__(self, timeout_seconds: int):
        message = f"API request timed out after {timeout_seconds} seconds"
        super().__init__(message)


class APISchemaError(MetadataPipelineError):
    """Raised when the LLM output fails schema validation."""
    
    def __init__(self, schema_name: str, validation_error: str, raw_output: str = None):
        self.schema_name = schema_name
        self.validation_error = validation_error
        self.raw_output = raw_output
        
        message = f"LLM output failed {schema_name} schema validation: {validation_error}"
        super().__init__(message)


class JSONParseError(MetadataPipelineError):
    """Raised when LLM output cannot be parsed as valid JSON."""
    
    def __init__(self, raw_output: str, parse_error: str):
        self.raw_output = raw_output
        self.parse_error = parse_error
        
        message = f"Failed to parse LLM JSON output: {parse_error}"
        super().__init__(message)


class CanonicalizationError(MetadataPipelineError):
    """Raised when failing to match with an external database."""
    
    def __init__(self, artist: str, title: str, reason: str = None):
        self.artist = artist
        self.title = title
        self.reason = reason
        
        message = f"Failed to canonicalize '{artist} - {title}'"
        if reason:
            message += f": {reason}"
        
        super().__init__(message)


class DatabaseError(CanonicalizationError):
    """Raised when external music database queries fail."""
    
    def __init__(self, database_name: str, query: str, error_details: str = None):
        self.database_name = database_name
        self.query = query
        self.error_details = error_details
        
        message = f"Failed to query {database_name} database for: {query}"
        if error_details:
            message += f" - {error_details}"
        
        super().__init__("", "", message)


class CacheError(MusicOrganizerError):
    """Raised when cache operations fail."""
    
    def __init__(self, cache_type: str, operation: str, reason: str = None):
        self.cache_type = cache_type
        self.operation = operation
        self.reason = reason
        
        message = f"Cache error in {cache_type} during {operation}"
        if reason:
            message += f": {reason}"
        
        super().__init__(message)


class FilesystemError(MusicOrganizerError):
    """Raised when filesystem operations fail."""
    
    def __init__(self, path: str, operation: str, reason: str = None):
        self.path = path
        self.operation = operation
        self.reason = reason
        
        message = f"Filesystem error during {operation} on {path}"
        if reason:
            message += f": {reason}"
        
        super().__init__(message)


class OrganizationError(MusicOrganizerError):
    """Raised when file organization/moving operations fail."""
    
    def __init__(self, source_path: str, dest_path: str, reason: str = None):
        self.source_path = source_path
        self.dest_path = dest_path
        self.reason = reason
        
        message = f"Failed to organize file from '{source_path}' to '{dest_path}'"
        if reason:
            message += f": {reason}"
        
        super().__init__(message)