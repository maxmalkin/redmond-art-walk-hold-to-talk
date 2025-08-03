"""
Queue management module for Raspberry Pi Audio System.

This module handles queue-based audio file processing including:
- Processing queue management
- Task prioritization and scheduling
- Inter-component communication
- Thread-safe queue operations
"""

from .file_queue import FileProcessingQueue

__all__ = ['FileProcessingQueue']