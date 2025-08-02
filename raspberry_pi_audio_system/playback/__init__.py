"""
Playback module for Raspberry Pi Audio System.

This module handles audio playback operations including:
- Multi-channel audio output management
- Channel-specific file playback
- Playback queue management
- Audio output device coordination
"""

from .output_manager import AudioOutputManager

__all__ = ['AudioOutputManager']