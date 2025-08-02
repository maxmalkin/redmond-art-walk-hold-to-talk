"""
Audio Device Manager for Raspberry Pi Audio System.

Manages USB audio devices:
- 1 USB microphone for input (MIC)
- 5 USB audio outputs (AUDIO_OUT_ONE through AUDIO_OUT_FIVE)

Provides audio device detection, configuration, and channel mapping.
"""

import logging
import subprocess
import re
import time
import os
from typing import Dict, List, Optional, Tuple, Union
import threading
import queue
from collections import defaultdict, deque
import json

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    # Mock pyaudio for development
    class MockStream:
        def __init__(self): 
            self.active = False
        def is_active(self): return self.active
        def start_stream(self): self.active = True
        def stop_stream(self): self.active = False
        def close(self): self.active = False
        def read(self, frames, exception_on_overflow=False): return b''
        def write(self, data): pass
    
    class MockPyAudio:
        paInt16 = 'paInt16'
        Stream = MockStream
        def __init__(self): pass
        def get_device_count(self): return 0
        def get_device_info_by_index(self, i): return {}
        def get_host_api_info_by_index(self, i): return {'name': 'Mock'}
        def open(self, **kwargs): return MockStream()
        def terminate(self): pass
    pyaudio = MockPyAudio()

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import pyusb
    import usb.core
    import usb.util
    USB_MONITORING_AVAILABLE = True
except ImportError:
    USB_MONITORING_AVAILABLE = False


class AudioDevice:
    """Represents an audio device with its properties."""
    
    def __init__(self, device_id: int, name: str, channels: int, sample_rate: int, is_input: bool):
        self.device_id = device_id
        self.name = name
        self.channels = channels
        self.sample_rate = sample_rate
        self.is_input = is_input
        self.is_available = True


class AudioDeviceManager:
    """
    Manages USB audio devices for the recording and playback system.
    
    Handles device detection, configuration, and provides interfaces for
    audio recording and playback operations.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize audio device manager.
        
        Args:
            config: Configuration dictionary containing audio settings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Audio configuration
        self.audio_config = config.get('audio', {})
        self.sample_rate = self.audio_config.get('sample_rate', 44100)
        self.chunk_size = self.audio_config.get('chunk_size', 1024)
        self.format = getattr(pyaudio, self.audio_config.get('format', 'paInt16'))
        self.channels = self.audio_config.get('channels', 1)
        
        # PyAudio instance
        self.pyaudio_instance = None
        
        # Device mappings
        self.input_device: Optional[AudioDevice] = None
        self.output_devices: Dict[int, AudioDevice] = {}  # channel -> device
        
        # Device locks for thread safety
        self.device_lock = threading.Lock()
        
        # Performance tracking
        self.performance_stats = {
            'device_changes': 0,
            'reconnections': 0,
            'failed_detections': 0,
            'stream_errors': 0,
            'total_streams_created': 0
        }
        
        # Device health monitoring
        self.device_health_checks = deque(maxlen=100)
        
        # Initialize audio system
        self._initialize_audio()
    
    def _initialize_audio(self):
        """Initialize PyAudio and detect devices."""
        try:
            if PYAUDIO_AVAILABLE:
                self.pyaudio_instance = pyaudio.PyAudio()
            else:
                self.pyaudio_instance = pyaudio  # pyaudio is already the mock instance
            self._detect_devices()
            self.logger.info("Audio system initialized successfully")
        except Exception as e:
            self.logger.error(f"Audio system initialization failed: {e}")
            raise
    
    def _detect_devices(self):
        """Detect and configure USB audio devices."""
        try:
            device_count = self.pyaudio_instance.get_device_count()
            self.logger.info(f"Detected {device_count} audio devices")
            
            # Lists to store detected devices
            input_devices = []
            output_devices = []
            
            for i in range(device_count):
                try:
                    device_info = self.pyaudio_instance.get_device_info_by_index(i)
                    device_name = device_info['name']
                    
                    # Skip non-USB devices (filter by name patterns)
                    if not self._is_usb_device(device_name):
                        continue
                    
                    # Create AudioDevice objects
                    if device_info['maxInputChannels'] > 0:
                        audio_device = AudioDevice(
                            device_id=i,
                            name=device_name,
                            channels=device_info['maxInputChannels'],
                            sample_rate=int(device_info['defaultSampleRate']),
                            is_input=True
                        )
                        input_devices.append(audio_device)
                        self.logger.info(f"Found USB input device: {device_name} (ID: {i})")
                    
                    if device_info['maxOutputChannels'] > 0:
                        audio_device = AudioDevice(
                            device_id=i,
                            name=device_name,
                            channels=device_info['maxOutputChannels'],
                            sample_rate=int(device_info['defaultSampleRate']),
                            is_input=False
                        )
                        output_devices.append(audio_device)
                        self.logger.info(f"Found USB output device: {device_name} (ID: {i})")
                
                except Exception as e:
                    self.logger.warning(f"Error processing device {i}: {e}")
            
            # Configure devices based on system requirements
            self._configure_devices(input_devices, output_devices)
            
        except Exception as e:
            self.logger.error(f"Device detection failed: {e}")
            raise
    
    def _is_usb_device(self, device_name: str) -> bool:
        """
        Determine if a device is a USB audio device based on name patterns.
        
        Args:
            device_name: Name of the audio device
            
        Returns:
            True if device appears to be USB audio device
        """
        usb_patterns = [
            r'usb',
            r'audio\s+adapter',
            r'headset',
            r'microphone',
            r'webcam',
            r'speaker'
        ]
        
        device_name_lower = device_name.lower()
        
        for pattern in usb_patterns:
            if re.search(pattern, device_name_lower):
                return True
        
        # Exclude built-in Pi audio
        if 'bcm' in device_name_lower or 'hdmi' in device_name_lower:
            return False
        
        return False
    
    def _configure_devices(self, input_devices: List[AudioDevice], output_devices: List[AudioDevice]):
        """
        Configure detected devices for system use.
        
        Args:
            input_devices: List of detected input devices
            output_devices: List of detected output devices
        """
        with self.device_lock:
            # Configure input device (microphone)
            if input_devices:
                # Use first suitable USB microphone
                self.input_device = input_devices[0]
                self.logger.info(f"Configured input device: {self.input_device.name}")
            else:
                self.logger.warning("No USB input devices found")
            
            # Configure output devices (speakers/headphones)
            self.output_devices = {}
            
            # Map output devices to channels (1-5)
            for channel in range(1, 6):
                if channel - 1 < len(output_devices):
                    self.output_devices[channel] = output_devices[channel - 1]
                    self.logger.info(f"Configured output channel {channel}: {output_devices[channel - 1].name}")
                else:
                    self.logger.warning(f"No output device available for channel {channel}")
    
    def get_input_device(self) -> Optional[AudioDevice]:
        """
        Get configured input device.
        
        Returns:
            AudioDevice for microphone input, or None if not available
        """
        with self.device_lock:
            return self.input_device
    
    def get_output_device(self, channel: int) -> Optional[AudioDevice]:
        """
        Get output device for specified channel.
        
        Args:
            channel: Output channel number (1-5)
            
        Returns:
            AudioDevice for output channel, or None if not available
        """
        with self.device_lock:
            return self.output_devices.get(channel)
    
    def create_input_stream(self, stream_id: Optional[str] = None) -> Optional[pyaudio.Stream]:
        """Create audio input stream for recording with enhanced error handling.
        
        Args:
            stream_id: Optional stream identifier for tracking
            
        Returns:
            PyAudio stream for recording, or None if not available
        """
        if not self.input_device or not self.input_device.is_available:
            self.logger.error("No input device configured or device unavailable")
            return None
        
        if stream_id is None:
            stream_id = f"input_{int(time.time() * 1000)}"
        
        try:
            start_time = time.time()
            
            # Configure stream parameters optimized for Pi
            stream_params = {
                'format': self.format,
                'channels': self.channels,
                'rate': self.sample_rate,
                'input': True,
                'input_device_index': self.input_device.device_id,
                'frames_per_buffer': self.chunk_size,
            }
            
            # Add Pi-specific optimizations
            if self._is_raspberry_pi():
                stream_params.update({
                    'start': False,  # Start manually for better control
                    'stream_callback': None  # Use blocking mode for more reliable operation
                })
            
            stream = self.pyaudio_instance.open(**stream_params)
            
            # Test the stream briefly
            if not stream.is_active():
                stream.start_stream()
                time.sleep(0.01)  # Brief test
                if not stream.is_active():
                    stream.close()
                    raise Exception("Stream failed to start")
            
            # Register stream for management
            self.stream_manager.register_stream(stream_id, stream)
            
            # Record performance metrics
            creation_time = time.time() - start_time
            self.input_device.record_usage(success=True, latency=creation_time)
            self.performance_stats['total_streams_created'] += 1
            
            self.logger.info(f"Created input stream {stream_id} on device: {self.input_device.name}")
            return stream
            
        except Exception as e:
            self.input_device.record_usage(success=False)
            self.performance_stats['stream_errors'] += 1
            self.logger.error(f"Failed to create input stream: {e}")
            
            # Try to refresh devices if stream creation fails
            if "device" in str(e).lower() or "invalid" in str(e).lower():
                self.logger.info("Attempting device refresh after stream failure")
                threading.Thread(target=self.refresh_devices, daemon=True).start()
            
            return None
    
    def create_output_stream(self, channel: int, stream_id: Optional[str] = None) -> Optional[pyaudio.Stream]:
        """Create audio output stream for playback with enhanced error handling.
        
        Args:
            channel: Output channel number (1-5)
            stream_id: Optional stream identifier for tracking
            
        Returns:
            PyAudio stream for playback, or None if not available
        """
        output_device = self.get_output_device(channel)
        if not output_device or not output_device.is_available:
            self.logger.error(f"No output device configured for channel {channel} or device unavailable")
            return None
        
        if stream_id is None:
            stream_id = f"output_{channel}_{int(time.time() * 1000)}"
        
        try:
            start_time = time.time()
            
            # Configure stream parameters optimized for Pi
            stream_params = {
                'format': self.format,
                'channels': self.channels,
                'rate': self.sample_rate,
                'output': True,
                'output_device_index': output_device.device_id,
                'frames_per_buffer': self.chunk_size,
            }
            
            # Add Pi-specific optimizations
            if self._is_raspberry_pi():
                stream_params.update({
                    'start': False,  # Start manually for better control
                    'stream_callback': None  # Use blocking mode for more reliable operation
                })
            
            stream = self.pyaudio_instance.open(**stream_params)
            
            # Test the stream briefly
            if not stream.is_active():
                stream.start_stream()
                time.sleep(0.01)  # Brief test
                if not stream.is_active():
                    stream.close()
                    raise Exception("Stream failed to start")
            
            # Register stream for management
            self.stream_manager.register_stream(stream_id, stream)
            
            # Record performance metrics
            creation_time = time.time() - start_time
            output_device.record_usage(success=True, latency=creation_time)
            self.performance_stats['total_streams_created'] += 1
            
            self.logger.info(f"Created output stream {stream_id} on channel {channel}: {output_device.name}")
            return stream
            
        except Exception as e:
            output_device.record_usage(success=False)
            self.performance_stats['stream_errors'] += 1
            self.logger.error(f"Failed to create output stream for channel {channel}: {e}")
            
            # Try to refresh devices if stream creation fails
            if "device" in str(e).lower() or "invalid" in str(e).lower():
                self.logger.info("Attempting device refresh after stream failure")
                threading.Thread(target=self.refresh_devices, daemon=True).start()
            
            return None
    
    def close_stream(self, stream_id: str) -> bool:
        """Close a specific stream by ID.
        
        Args:
            stream_id: Stream identifier
            
        Returns:
            True if stream was closed successfully
        """
        return self.stream_manager.close_stream(stream_id)
    
    def get_active_streams(self) -> Dict[str, pyaudio.Stream]:
        """Get all currently active streams.
        
        Returns:
            Dictionary of stream IDs to stream objects
        """
        with self.stream_manager.stream_lock:
            return self.stream_manager.active_streams.copy()
    
    def test_device_connectivity(self) -> Dict[str, bool]:
        """
        Test connectivity of all configured devices.
        
        Returns:
            Dictionary with device test results
        """
        results = {}
        
        # Test input device
        if self.input_device:
            try:
                stream = self.create_input_stream()
                if stream:
                    stream.close()
                    results['input'] = True
                else:
                    results['input'] = False
            except Exception:
                results['input'] = False
        else:
            results['input'] = False
        
        # Test output devices
        for channel in range(1, 6):
            try:
                stream = self.create_output_stream(channel)
                if stream:
                    stream.close()
                    results[f'output_channel_{channel}'] = True
                else:
                    results[f'output_channel_{channel}'] = False
            except Exception:
                results[f'output_channel_{channel}'] = False
        
        return results
    
    def get_device_info(self) -> Dict:
        """Get comprehensive information about all configured devices.
        
        Returns:
            Dictionary containing detailed device information
        """
        with self.device_lock:
            info = {
                'timestamp': time.time(),
                'input_device': None,
                'output_devices': {},
                'audio_config': {
                    'sample_rate': self.sample_rate,
                    'chunk_size': self.chunk_size,
                    'format': str(self.format),
                    'channels': self.channels,
                    'max_recording_duration': getattr(self, 'max_recording_duration', 300)
                },
                'system_info': {
                    'total_devices': len(self.output_devices) + (1 if self.input_device else 0),
                    'active_streams': len(getattr(self, 'stream_manager', {}).get('active_streams', {})),
                    'is_raspberry_pi': self._is_raspberry_pi() if hasattr(self, '_is_raspberry_pi') else False,
                    'monitoring_enabled': getattr(self, 'monitoring_enabled', False)
                },
                'performance_stats': self.get_performance_stats() if hasattr(self, 'get_performance_stats') else {},
                'device_health': {}
            }
            
            if self.input_device:
                if hasattr(self.input_device, 'to_dict'):
                    info['input_device'] = self.input_device.to_dict()
                else:
                    info['input_device'] = {
                        'device_id': self.input_device.device_id,
                        'name': self.input_device.name,
                        'channels': self.input_device.channels,
                        'sample_rate': self.input_device.sample_rate
                    }
                info['device_health']['input'] = self.input_device.get_health_score() if hasattr(self.input_device, 'get_health_score') else 1.0
            
            for channel, device in self.output_devices.items():
                if hasattr(device, 'to_dict'):
                    info['output_devices'][channel] = device.to_dict()
                else:
                    info['output_devices'][channel] = {
                        'device_id': device.device_id,
                        'name': device.name,
                        'channels': device.channels,
                        'sample_rate': device.sample_rate
                    }
                info['device_health'][f'output_{channel}'] = device.get_health_score() if hasattr(device, 'get_health_score') else 1.0
            
            return info
    
    def refresh_devices(self):
        """Refresh device detection and configuration."""
        self.logger.info("Refreshing audio device detection")
        self._detect_devices()
    
    def get_microphone_device(self) -> Optional[AudioDevice]:
        """Get configured microphone device (alias for get_input_device).
        
        Returns:
            AudioDevice for microphone input, or None if not available
        """
        return self.get_input_device()
    
    def start_recording_stream(self, callback: Optional[callable] = None) -> Optional[str]:
        """Start recording stream with callback support.
        
        Args:
            callback: Optional callback function for audio data
            
        Returns:
            Stream ID if successful, None otherwise
        """
        stream_id = f"recording_{int(time.time() * 1000)}"
        stream = self.create_input_stream(stream_id)
        
        if stream:
            # If callback provided, could set up callback processing here
            # For now, return the stream ID for manual management
            return stream_id
        
        return None
    
    def start_playback_stream(self, channel: int, callback: Optional[callable] = None) -> Optional[str]:
        """Start playback stream for specific channel.
        
        Args:
            channel: Output channel number (1-5)
            callback: Optional callback function for audio data
            
        Returns:
            Stream ID if successful, None otherwise
        """
        stream_id = f"playback_{channel}_{int(time.time() * 1000)}"
        stream = self.create_output_stream(channel, stream_id)
        
        if stream:
            # If callback provided, could set up callback processing here
            # For now, return the stream ID for manual management
            return stream_id
        
        return None
    
    def stop_stream(self, stream_id: str) -> bool:
        """Stop and close a stream by ID.
        
        Args:
            stream_id: Stream identifier
            
        Returns:
            True if stream was stopped successfully
        """
        return self.close_stream(stream_id)

    def get_performance_stats(self) -> Dict:
        """Get audio system performance statistics.
        
        Returns:
            Dictionary with performance metrics
        """
        stats = self.performance_stats.copy()
        
        # Add device-specific performance data
        if self.input_device:
            stats['input_device_health'] = self.input_device.get_health_score()
            stats['input_device_uses'] = self.input_device.total_uses
            stats['input_device_errors'] = self.input_device.error_count
        
        stats['output_device_health'] = {}
        for channel, device in self.output_devices.items():
            stats['output_device_health'][channel] = {
                'health_score': device.get_health_score(),
                'total_uses': device.total_uses,
                'error_count': device.error_count,
                'avg_latency': device.avg_latency
            }
        
        # Add recent health check results
        if self.device_health_checks:
            latest_health = self.device_health_checks[-1]
            stats['latest_health_check'] = latest_health['timestamp']
            stats['device_health_summary'] = {
                name: result['healthy'] for name, result in latest_health['devices'].items()
            }
        
        return stats

    def optimize_for_low_latency(self):
        """Apply optimizations for low-latency audio processing."""
        if not self._is_raspberry_pi():
            self.logger.info("Low-latency optimizations are Pi-specific")
            return
        
        try:
            # Reduce chunk size for lower latency
            self.chunk_size = min(512, self.chunk_size)
            
            # Apply system-level optimizations
            optimizations = [
                'echo noop > /sys/block/mmcblk0/queue/scheduler',  # Disable disk scheduler
                'echo 1 > /proc/sys/kernel/sched_rt_runtime_us',  # Enable RT scheduling
            ]
            
            for cmd in optimizations:
                try:
                    os.system(f'sudo sh -c "{cmd}" 2>/dev/null')
                except:
                    pass
            
            self.logger.info("Applied low-latency optimizations")
            
        except Exception as e:
            self.logger.warning(f"Could not apply all low-latency optimizations: {e}")

    def emergency_stop_all_streams(self):
        """Emergency stop all active audio streams."""
        try:
            self.stream_manager.close_all_streams()
            self.logger.warning("Emergency stopped all audio streams")
        except Exception as e:
            self.logger.error(f"Error in emergency stream stop: {e}")
    
    def cleanup(self):
        """Clean up audio resources."""
        try:
            if self.pyaudio_instance:
                self.pyaudio_instance.terminate()
            self.logger.info("Audio device manager cleanup completed")
        except Exception as e:
            self.logger.error(f"Audio cleanup failed: {e}")