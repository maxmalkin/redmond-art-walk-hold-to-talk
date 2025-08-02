"""
Utilities module for Raspberry Pi Audio System.

This module provides utility functions and classes including:
- File management and organization
- Configuration loading and validation
- System utilities and helpers
- Logging configuration
"""

from .file_manager import FileManager
from .config import ConfigManager

__all__ = ['FileManager', 'ConfigManager']