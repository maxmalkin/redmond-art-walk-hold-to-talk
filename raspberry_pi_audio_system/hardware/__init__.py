"""
Hardware module for Raspberry Pi Audio System.

This module handles all hardware interfaces including:
- GPIO button management (recording and playback buttons)
- USB audio device management
- Hardware abstraction layer for audio I/O
"""

from .gpio_handler import GPIOHandler
from .audio_devices import AudioDeviceManager

__all__ = ['GPIOHandler', 'AudioDeviceManager']