"""
Resilient OpenAI API client with comprehensive error handling and caching.

This module implements a robust API client that handles retries, rate limiting,
JSON parsing errors, and includes a self-correction mechanism for malformed responses.
"""

import json
import time
import hashlib
import logging
import re
from typing import Dict, Any, Optional, Type, TypeVar
from pathlib import Path

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from utils.exceptions import (
    APICommunicationError, APIRateLimitError, APITimeoutError, 
    APISchemaError, JSONParseError
)

logger = logging.getLogger(__name__)

# Type variable for Pydantic models
T = TypeVar('T', bound=BaseModel)


class ResilientAPIClient:
    """
    A resilient OpenAI API client with retry logic, caching, and error recovery.
    """
    
    def __init__(
        self,
        api_key: str = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        timeout: float = 30.0
    ):
        """
        Initialize the API client.
        
        Args:
            api_key: OpenAI API key (if None, will use environment variable)
            max_retries: Maximum number of retry attempts
            base_delay: Base delay for exponential backoff (seconds)
            max_delay: Maximum delay between retries (seconds)
            timeout: Request timeout in seconds
        """
        self.client = OpenAI(api_key=api_key)
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        
        # Statistics
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.retried_requests = 0
    
    def get_structured_response(
        self,
        prompt: str,
        model: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 1000
    ) -> T:
        """
        Get a structured response from the LLM that conforms to a Pydantic schema.
        
        Args:
            prompt: The prompt to send to the model
            model: Model name (e.g., "gpt-4o-mini", "gpt-4o")
            response_model: Pydantic model class for response validation
            temperature: Sampling temperature (0.0 for deterministic)
            max_tokens: Maximum tokens in response
            
        Returns:
            Validated Pydantic model instance
            
        Raises:
            APICommunicationError: For network/API errors
            APISchemaError: For schema validation failures
            JSONParseError: For JSON parsing failures
        """
        self.total_requests += 1
        
        # Generate JSON schema from Pydantic model
        schema = response_model.model_json_schema()
        
        # Enhanced prompt with schema
        enhanced_prompt = self._build_structured_prompt(prompt, schema)
        
        logger.debug(f"Making API request to model: {model}")
        
        # Sanitize Unicode characters to prevent encoding errors
        enhanced_prompt = self._sanitize_unicode(enhanced_prompt)
        
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"API request attempt {attempt + 1}/{self.max_retries + 1}")
                
                # Use appropriate parameter based on model
                completion_params = {
                    "model": model,
                    "messages": [{"role": "user", "content": enhanced_prompt}],
                    "timeout": self.timeout
                }
                
                # GPT-5 models have different parameter requirements
                if model.startswith("gpt-5"):
                    # GPT-5 models need more tokens for completion
                    # Different variants may have different optimal token counts
                    if "nano" in model:
                        # gpt-5-nano might be more concise
                        gpt5_max_tokens = max(max_tokens * 2, 2000)  # At least 2000 tokens
                    elif "mini" in model:
                        # gpt-5-mini needs moderate token count
                        gpt5_max_tokens = max(max_tokens * 3, 3000)  # At least 3000 tokens
                    else:
                        # Full gpt-5 needs more tokens
                        gpt5_max_tokens = max(max_tokens * 4, 4000)  # At least 4000 tokens
                    
                    completion_params["max_completion_tokens"] = gpt5_max_tokens
                    logger.debug(f"Using token limit for {model}: {gpt5_max_tokens}")
                    
                    # GPT-5 only supports default temperature (1), not 0.0
                    if temperature != 0.0 and temperature != 1.0:
                        logger.debug(f"GPT-5 doesn't support temperature={temperature}, using default (1.0)")
                    # For deterministic output with GPT-5, we rely on the model's default behavior
                    # Don't set temperature parameter at all to use default
                    
                    # Try with response_format for GPT-5 to ensure JSON output
                    completion_params["response_format"] = {"type": "json_object"}
                    logger.debug("Using GPT-5 with JSON response_format")
                else:
                    completion_params["max_tokens"] = max_tokens
                    completion_params["temperature"] = temperature
                    completion_params["response_format"] = {"type": "json_object"}
                
                response = self.client.chat.completions.create(**completion_params)
                
                # Log response details for debugging
                logger.debug(f"API Response - Model: {model}, Choices: {len(response.choices)}")
                
                # Extract content
                if not response.choices:
                    raise APISchemaError(
                        response_model.__name__,
                        "No choices in API response",
                        str(response)
                    )
                
                choice = response.choices[0]
                content = choice.message.content
                
                # Log the message structure for debugging
                logger.debug(f"Message type: {type(choice.message)}, Has content: {hasattr(choice.message, 'content')}")
                logger.debug(f"Response content length: {len(content) if content else 0}")
                
                if not content:
                    # Check the finish reason to understand why content is empty
                    finish_reason = choice.finish_reason
                    logger.error(f"Empty content - Finish reason: {finish_reason}")
                    logger.error(f"Empty content - Message: {choice.message}")
                    
                    if finish_reason == 'length':
                        error_msg = f"Response truncated due to token limit. Model: {model}, Current limit: {completion_params.get('max_completion_tokens', completion_params.get('max_tokens', 'unknown'))}"
                        logger.error(error_msg)
                        raise APISchemaError(
                            response_model.__name__,
                            error_msg,
                            str(choice)
                        )
                    
                    # Try to get any available text from the response
                    if hasattr(choice.message, 'text'):
                        content = choice.message.text
                        logger.debug(f"Found content in 'text' field: {len(content) if content else 0} chars")
                    
                    if not content:
                        raise APISchemaError(
                            response_model.__name__,
                            f"Empty response content (finish_reason: {finish_reason})",
                            str(choice.message)
                        )
                
                # Parse and validate JSON
                try:
                    parsed_data = json.loads(content)
                    validated_response = response_model.model_validate(parsed_data)
                    
                    self.successful_requests += 1
                    if attempt > 0:
                        self.retried_requests += 1
                    
                    logger.debug(f"Successful API response after {attempt + 1} attempts")
                    return validated_response
                    
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON parse error: {e}")
                    
                    # Try to repair the JSON
                    repaired_content = self._attempt_json_repair(content, str(e), model)
                    if repaired_content:
                        try:
                            parsed_data = json.loads(repaired_content)
                            validated_response = response_model.model_validate(parsed_data)
                            
                            self.successful_requests += 1
                            if attempt > 0:
                                self.retried_requests += 1
                            
                            logger.info("Successfully repaired and validated JSON response")
                            return validated_response
                            
                        except (json.JSONDecodeError, ValidationError) as repair_error:
                            logger.error(f"JSON repair failed: {repair_error}")
                    
                    # If repair fails and we're out of retries, raise error
                    if attempt == self.max_retries:
                        raise JSONParseError(content, str(e))
                
                except ValidationError as e:
                    logger.warning(f"Schema validation error: {e}")
                    
                    # If validation fails and we're out of retries, raise error
                    if attempt == self.max_retries:
                        raise APISchemaError(
                            response_model.__name__,
                            str(e),
                            content
                        )
            
            except openai.RateLimitError as e:
                retry_after = getattr(e, 'retry_after', None) or 60
                logger.warning(f"Rate limit hit, waiting {retry_after} seconds")
                
                if attempt < self.max_retries:
                    time.sleep(retry_after)
                    continue
                else:
                    raise APIRateLimitError(retry_after)
            
            except openai.APITimeoutError as e:
                logger.warning(f"Request timeout: {e}")
                
                if attempt < self.max_retries:
                    delay = self._calculate_backoff_delay(attempt)
                    time.sleep(delay)
                    continue
                else:
                    raise APITimeoutError(self.timeout)
            
            except openai.APIStatusError as e:
                status_code = e.status_code
                logger.warning(f"API status error {status_code}: {e}")
                
                # Retry on server errors (5xx) but not client errors (4xx)
                if 500 <= status_code < 600 and attempt < self.max_retries:
                    delay = self._calculate_backoff_delay(attempt)
                    time.sleep(delay)
                    continue
                else:
                    raise APICommunicationError(str(e), status_code)
            
            except Exception as e:
                logger.error(f"Unexpected API error: {e}")
                
                if attempt < self.max_retries:
                    delay = self._calculate_backoff_delay(attempt)
                    time.sleep(delay)
                    continue
                else:
                    raise APICommunicationError(str(e))
        
        # This should never be reached, but just in case
        self.failed_requests += 1
        raise APICommunicationError("Max retries exceeded")
    
    def _build_structured_prompt(self, prompt: str, schema: Dict[str, Any]) -> str:
        """Build an enhanced prompt with JSON schema instructions."""
        # Extract required fields from schema
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        
        # Build field descriptions
        field_descriptions = []
        for field_name, field_info in properties.items():
            description = field_info.get("description", "")
            field_type = field_info.get("type", "string")
            is_required = field_name in required_fields
            
            req_str = " (required)" if is_required else " (optional)"
            field_descriptions.append(f"- {field_name}: {field_type}{req_str} - {description}")
        
        fields_str = "\n".join(field_descriptions)
        
        return f"""
{prompt}

IMPORTANT: You must respond with a valid JSON object with these fields:

{fields_str}

Requirements:
- Respond ONLY with the JSON object, no additional text
- Include all required fields
- Use appropriate data types (strings, numbers, arrays, etc.)

Example format:
{{
  "field1": "value1",
  "field2": 123,
  "field3": ["item1", "item2"]
}}

Your JSON response:"""
    
    def _attempt_json_repair(
        self, 
        malformed_json: str, 
        error_message: str, 
        model: str
    ) -> Optional[str]:
        """
        Attempt to repair malformed JSON using a smaller model.
        
        Args:
            malformed_json: The malformed JSON string
            error_message: The original parsing error message
            model: Original model name (we'll use a cheaper model for repair)
            
        Returns:
            Repaired JSON string, or None if repair failed
        """
        # Use a fast, cheap model for repair
        repair_model = "gpt-4o-mini" if "gpt-4o" in model else model
        
        repair_prompt = f"""
The following text was intended to be a valid JSON object, but it failed to parse with this error:
{error_message}

Malformed JSON:
{malformed_json}

Please fix the syntax errors and return only the corrected JSON object. Do not add any commentary or explanation.

Corrected JSON:"""
        
        try:
            # Use appropriate parameter based on model
            repair_params = {
                "model": repair_model,
                "messages": [{"role": "user", "content": repair_prompt}],
                "timeout": 15.0  # Shorter timeout for repair
            }
            
            # GPT-5 models have different parameter requirements
            if repair_model.startswith("gpt-5"):
                # Use appropriate token limits for different GPT-5 variants
                if "nano" in repair_model:
                    repair_params["max_completion_tokens"] = max(len(malformed_json) + 500, 2000)
                elif "mini" in repair_model:
                    repair_params["max_completion_tokens"] = max(len(malformed_json) + 750, 3000)
                else:
                    repair_params["max_completion_tokens"] = max(len(malformed_json) + 1000, 4000)
                # GPT-5 doesn't support temperature=0.0, use default
            else:
                repair_params["max_tokens"] = len(malformed_json) + 100
                repair_params["temperature"] = 0.0
            
            response = self.client.chat.completions.create(**repair_params)
            
            repaired_content = response.choices[0].message.content
            if repaired_content:
                # Clean up the response (remove code fences if present)
                repaired_content = self._clean_json_response(repaired_content)
                logger.info("Attempted JSON repair")
                return repaired_content
        
        except Exception as e:
            logger.error(f"JSON repair attempt failed: {e}")
        
        return None
    
    def _clean_json_response(self, response: str) -> str:
        """Clean up JSON response by removing code fences and extra text."""
        # Remove markdown code fences
        response = re.sub(r'```json\s*', '', response)
        response = re.sub(r'```\s*$', '', response)
        
        # Find the JSON object boundaries
        first_brace = response.find('{')
        last_brace = response.rfind('}')
        
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return response[first_brace:last_brace + 1]
        
        return response.strip()
    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        # Exponential backoff: base_delay * 2^attempt
        delay = self.base_delay * (2 ** attempt)
        
        # Add jitter (random variation up to 25% of delay)
        import random
        jitter = delay * 0.25 * random.random()
        delay += jitter
        
        # Cap at maximum delay
        return min(delay, self.max_delay)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get client usage statistics."""
        success_rate = (
            self.successful_requests / self.total_requests * 100
            if self.total_requests > 0 else 0
        )
        
        return {
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'retried_requests': self.retried_requests,
            'success_rate_percent': round(success_rate, 2)
        }
    
    def _sanitize_unicode(self, text: str) -> str:
        """
        Sanitize Unicode text to prevent encoding errors.
        
        This removes or replaces problematic Unicode characters that can cause
        UTF-8 encoding errors when sending to the API, particularly surrogate
        characters and other invalid sequences.
        
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
                    # Use the Unicode replacement character or a simple placeholder
                    sanitized_chars.append('?')
            
            sanitized_text = ''.join(sanitized_chars)
            logger.debug(f"Sanitized text: removed/replaced {len(text) - len([c for c in sanitized_chars if c != '?'])} problematic characters")
            
            return sanitized_text