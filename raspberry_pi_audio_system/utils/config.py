"""
Configuration Manager for Raspberry Pi Audio System.

Handles loading, validation, and management of system configuration
from YAML files and environment variables.
"""

import yaml
import os
import logging
from typing import Dict, Any, Optional, List
import json
from datetime import datetime


class ConfigurationError(Exception):
    """Exception raised for configuration-related errors."""
    pass


class ConfigManager:
    """
    Configuration manager for the audio system.
    
    Loads and validates configuration from YAML files,
    supports environment variable overrides, and provides
    configuration validation and management.
    """
    
    def __init__(self, config_file_path: str = "config.yaml"):
        """
        Initialize configuration manager.
        
        Args:
            config_file_path: Path to main configuration file
        """
        self.config_file_path = config_file_path
        self.logger = logging.getLogger(__name__)
        
        # Configuration data
        self.config: Dict[str, Any] = {}
        self.defaults: Dict[str, Any] = {}
        
        # Load default configuration
        self._load_defaults()
        
        # Load configuration from file
        self.load_config()
    
    def _load_defaults(self):
        """Load default configuration values."""
        self.defaults = {
            # GPIO Configuration
            'gpio': {
                'recording_buttons': {
                    'REC_BUTTON_ONE': 2,
                    'REC_BUTTON_TWO': 3,
                    'REC_BUTTON_THREE': 4,
                    'REC_BUTTON_FOUR': 17,
                    'REC_BUTTON_FIVE': 27
                },
                'playback_buttons': {
                    'PHONE_UP_ONE': 22,
                    'PHONE_UP_TWO': 10,
                    'PHONE_UP_THREE': 9,
                    'PHONE_UP_FOUR': 11,
                    'PHONE_UP_FIVE': 5
                }
            },
            
            # Audio Configuration
            'audio': {
                'sample_rate': 44100,
                'chunk_size': 1024,
                'format': 'paInt16',
                'channels': 1,
                'max_recording_duration': 300  # 5 minutes
            },
            
            # spchcat Configuration (MANDATORY)
            'spchcat': {
                'binary_path': '/usr/local/bin/spchcat',
                'model_path': '/usr/local/share/spchcat/models',
                'language': 'en',
                'timeout': 30,
                'confidence_threshold': 0.7,
                'extra_options': []
            },
            
            # Content Filter Configuration
            'content_filter': {
                'strict_mode': True,
                'case_sensitive': False,
                'filtered_words': [
                    'inappropriate',
                    'profanity',
                    'offensive'
                ],
                'filtered_phrases': [
                    'inappropriate phrase'
                ]
            },
            
            # File Paths
            'paths': {
                'recordings': './recordings',
                'temp': './temp',
                'bin': './bin',
                'playable': './playable',
                'logs': './logs',
                'backup': './backup'
            },
            
            # Queue Configuration
            'queue': {
                'max_size': 100,
                'max_workers': 2,
                'processing_timeout': 120
            },
            
            # File Management
            'file_management': {
                'max_temp_age_hours': 1,
                'max_log_age_days': 30,
                'backup_enabled': True,
                'max_files_per_channel': 100
            },
            
            # Logging Configuration
            'logging': {
                'level': 'INFO',
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                'file': './logs/system.log',
                'max_size_mb': 10,
                'backup_count': 5
            },
            
            # System Configuration
            'system': {
                'enable_hardware_monitoring': True,
                'heartbeat_interval': 30,
                'auto_cleanup_interval': 3600,  # 1 hour
                'startup_delay': 2
            }
        }
    
    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration from file with defaults fallback.
        
        Returns:
            Loaded configuration dictionary
        """
        try:
            # Start with defaults
            self.config = self._deep_copy_dict(self.defaults)
            
            # Load from file if it exists
            if os.path.exists(self.config_file_path):
                with open(self.config_file_path, 'r') as file:
                    file_config = yaml.safe_load(file)
                    if file_config:
                        self.config = self._merge_configs(self.config, file_config)
                        self.logger.info(f"Loaded configuration from {self.config_file_path}")
                    else:
                        self.logger.warning(f"Configuration file {self.config_file_path} is empty, using defaults")
            else:
                self.logger.warning(f"Configuration file {self.config_file_path} not found, using defaults")
            
            # Apply environment variable overrides
            self._apply_env_overrides()
            
            # Validate configuration
            self._validate_config()
            
            return self.config
            
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise ConfigurationError(f"Configuration loading failed: {e}")
    
    def _deep_copy_dict(self, source: Dict) -> Dict:
        """Create a deep copy of a dictionary."""
        import copy
        return copy.deepcopy(source)
    
    def _merge_configs(self, base: Dict, override: Dict) -> Dict:
        """
        Recursively merge configuration dictionaries.
        
        Args:
            base: Base configuration
            override: Configuration to merge in
            
        Returns:
            Merged configuration
        """
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _apply_env_overrides(self):
        """Apply environment variable overrides to configuration."""
        try:
            # Define environment variable mappings
            env_mappings = {
                'AUDIO_SAMPLE_RATE': ('audio', 'sample_rate', int),
                'AUDIO_CHUNK_SIZE': ('audio', 'chunk_size', int),
                'SPCHCAT_BINARY_PATH': ('spchcat', 'binary_path', str),
                'SPCHCAT_MODEL_PATH': ('spchcat', 'model_path', str),
                'SPCHCAT_LANGUAGE': ('spchcat', 'language', str),
                'LOG_LEVEL': ('logging', 'level', str),
                'MAX_FILES_PER_CHANNEL': ('file_management', 'max_files_per_channel', int),
                'BACKUP_ENABLED': ('file_management', 'backup_enabled', bool)
            }
            
            for env_var, (section, key, type_func) in env_mappings.items():
                env_value = os.getenv(env_var)
                if env_value is not None:
                    try:
                        if type_func == bool:
                            converted_value = env_value.lower() in ('true', '1', 'yes', 'on')
                        else:
                            converted_value = type_func(env_value)
                        
                        self.config[section][key] = converted_value
                        self.logger.info(f"Applied environment override {env_var}={converted_value}")
                    except (ValueError, TypeError) as e:
                        self.logger.warning(f"Invalid environment variable {env_var}={env_value}: {e}")
            
        except Exception as e:
            self.logger.error(f"Failed to apply environment overrides: {e}")
    
    def _validate_config(self):
        """Validate configuration for required values and constraints."""
        try:
            errors = []
            
            # Validate GPIO pins
            self._validate_gpio_config(errors)
            
            # Validate audio settings
            self._validate_audio_config(errors)
            
            # Validate spchcat configuration
            self._validate_spchcat_config(errors)
            
            # Validate file paths
            self._validate_paths_config(errors)
            
            # Validate queue settings
            self._validate_queue_config(errors)
            
            if errors:
                error_message = "Configuration validation failed:\n" + "\n".join(errors)
                raise ConfigurationError(error_message)
            
            self.logger.info("Configuration validation successful")
            
        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(f"Configuration validation error: {e}")
    
    def _validate_gpio_config(self, errors: List[str]):
        """Validate GPIO configuration."""
        gpio_config = self.config.get('gpio', {})
        
        # Check recording buttons
        recording_buttons = gpio_config.get('recording_buttons', {})
        if len(recording_buttons) != 5:
            errors.append("Must have exactly 5 recording buttons configured")
        
        # Check playback buttons
        playback_buttons = gpio_config.get('playback_buttons', {})
        if len(playback_buttons) != 5:
            errors.append("Must have exactly 5 playback buttons configured")
        
        # Check for pin conflicts
        all_pins = list(recording_buttons.values()) + list(playback_buttons.values())
        if len(set(all_pins)) != len(all_pins):
            errors.append("GPIO pin conflicts detected - pins must be unique")
        
        # Validate pin numbers (BCM numbering)
        valid_pins = list(range(2, 28))  # Raspberry Pi BCM pins
        for pin in all_pins:
            if not isinstance(pin, int) or pin not in valid_pins:
                errors.append(f"Invalid GPIO pin number: {pin}")
    
    def _validate_audio_config(self, errors: List[str]):
        """Validate audio configuration."""
        audio_config = self.config.get('audio', {})
        
        # Sample rate validation
        sample_rate = audio_config.get('sample_rate', 44100)
        if not isinstance(sample_rate, int) or sample_rate < 8000 or sample_rate > 192000:
            errors.append(f"Invalid sample rate: {sample_rate}")
        
        # Chunk size validation
        chunk_size = audio_config.get('chunk_size', 1024)
        if not isinstance(chunk_size, int) or chunk_size < 64 or chunk_size > 8192:
            errors.append(f"Invalid chunk size: {chunk_size}")
        
        # Format validation
        valid_formats = ['paInt8', 'paInt16', 'paInt24', 'paInt32', 'paFloat32']
        audio_format = audio_config.get('format', 'paInt16')
        if audio_format not in valid_formats:
            errors.append(f"Invalid audio format: {audio_format}")
    
    def _validate_spchcat_config(self, errors: List[str]):
        """Validate spchcat configuration."""
        spchcat_config = self.config.get('spchcat', {})
        
        # Binary path validation
        binary_path = spchcat_config.get('binary_path', '')
        if not binary_path:
            errors.append("spchcat binary path is required")
        
        # Model path validation
        model_path = spchcat_config.get('model_path', '')
        if not model_path:
            errors.append("spchcat model path is required")
        
        # Timeout validation
        timeout = spchcat_config.get('timeout', 30)
        if not isinstance(timeout, int) or timeout < 5 or timeout > 300:
            errors.append(f"Invalid spchcat timeout: {timeout}")
        
        # Confidence threshold validation
        confidence = spchcat_config.get('confidence_threshold', 0.7)
        if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
            errors.append(f"Invalid confidence threshold: {confidence}")
    
    def _validate_paths_config(self, errors: List[str]):
        """Validate file paths configuration."""
        paths_config = self.config.get('paths', {})
        
        required_paths = ['recordings', 'temp', 'bin', 'playable', 'logs']
        for path_name in required_paths:
            if path_name not in paths_config:
                errors.append(f"Missing required path: {path_name}")
    
    def _validate_queue_config(self, errors: List[str]):
        """Validate queue configuration."""
        queue_config = self.config.get('queue', {})
        
        # Max size validation
        max_size = queue_config.get('max_size', 100)
        if not isinstance(max_size, int) or max_size < 10 or max_size > 1000:
            errors.append(f"Invalid queue max size: {max_size}")
        
        # Max workers validation
        max_workers = queue_config.get('max_workers', 2)
        if not isinstance(max_workers, int) or max_workers < 1 or max_workers > 10:
            errors.append(f"Invalid queue max workers: {max_workers}")
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value by dotted key path.
        
        Args:
            key_path: Dotted key path (e.g., 'audio.sample_rate')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        try:
            keys = key_path.split('.')
            value = self.config
            
            for key in keys:
                value = value[key]
            
            return value
            
        except (KeyError, TypeError):
            return default
    
    def set(self, key_path: str, value: Any):
        """
        Set configuration value by dotted key path.
        
        Args:
            key_path: Dotted key path (e.g., 'audio.sample_rate')
            value: Value to set
        """
        keys = key_path.split('.')
        config_ref = self.config
        
        # Navigate to parent of target key
        for key in keys[:-1]:
            if key not in config_ref:
                config_ref[key] = {}
            config_ref = config_ref[key]
        
        # Set the value
        config_ref[keys[-1]] = value
        self.logger.info(f"Updated configuration: {key_path} = {value}")
    
    def save_config(self, file_path: Optional[str] = None) -> bool:
        """
        Save current configuration to file.
        
        Args:
            file_path: Optional file path (uses default if not provided)
            
        Returns:
            True if save was successful
        """
        try:
            save_path = file_path or self.config_file_path
            
            with open(save_path, 'w') as file:
                yaml.dump(self.config, file, default_flow_style=False, indent=2)
            
            self.logger.info(f"Configuration saved to {save_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save configuration: {e}")
            return False
    
    def export_config(self, format_type: str = 'yaml') -> str:
        """
        Export configuration in specified format.
        
        Args:
            format_type: Export format ('yaml' or 'json')
            
        Returns:
            Configuration as formatted string
        """
        try:
            if format_type.lower() == 'json':
                return json.dumps(self.config, indent=2, default=str)
            else:
                return yaml.dump(self.config, default_flow_style=False, indent=2)
                
        except Exception as e:
            self.logger.error(f"Failed to export configuration: {e}")
            return ""
    
    def get_config_summary(self) -> Dict[str, Any]:
        """
        Get summary of current configuration.
        
        Returns:
            Configuration summary dictionary
        """
        try:
            return {
                'config_file': self.config_file_path,
                'loaded_at': datetime.now().isoformat(),
                'gpio_pins_used': len(self.config['gpio']['recording_buttons']) + len(self.config['gpio']['playback_buttons']),
                'audio_sample_rate': self.config['audio']['sample_rate'],
                'spchcat_enabled': bool(self.config['spchcat']['binary_path']),
                'content_filter_enabled': bool(self.config['content_filter']['filtered_words']),
                'backup_enabled': self.config['file_management']['backup_enabled'],
                'log_level': self.config['logging']['level']
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get configuration summary: {e}")
            return {}
    
    def validate_runtime_requirements(self) -> List[str]:
        """
        Validate runtime requirements and return any issues.
        
        Returns:
            List of validation issues (empty if all good)
        """
        issues = []
        
        try:
            # Check spchcat binary
            spchcat_path = self.config['spchcat']['binary_path']
            if not os.path.exists(spchcat_path):
                issues.append(f"spchcat binary not found: {spchcat_path}")
            elif not os.access(spchcat_path, os.X_OK):
                issues.append(f"spchcat binary not executable: {spchcat_path}")
            
            # Check spchcat model directory
            model_path = self.config['spchcat']['model_path']
            if not os.path.exists(model_path):
                issues.append(f"spchcat model directory not found: {model_path}")
            
            # Check required directories
            for path_name, path_value in self.config['paths'].items():
                parent_dir = os.path.dirname(path_value)
                if parent_dir and not os.path.exists(parent_dir):
                    issues.append(f"Parent directory for {path_name} does not exist: {parent_dir}")
            
        except Exception as e:
            issues.append(f"Runtime validation error: {e}")
        
        return issues
    
    def reload_config(self) -> bool:
        """
        Reload configuration from file.
        
        Returns:
            True if reload was successful
        """
        try:
            self.load_config()
            self.logger.info("Configuration reloaded successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to reload configuration: {e}")
            return False