"""
Processing module for Raspberry Pi Audio System.

This module handles all audio processing operations including:
- Audio recording from USB microphone
- Speech-to-text processing using spchcat library (MANDATORY)
- Content filtering for inappropriate words
- Audio file format handling and conversion
"""

from .recorder import AudioRecorder
from .speech_processor import SpeechProcessor
from .content_filter import ContentFilter

__all__ = ['AudioRecorder', 'SpeechProcessor', 'ContentFilter']