"""
Audio Recorder for Raspberry Pi Audio System.

Handles real-time audio recording from USB microphone with proper
channel management and file organization.
"""

import threading
import time
import wave
import logging
import os
from typing import Optional, Callable, Dict, List, Union
from datetime import datetime
import pyaudio
from enum import Enum
from collections import deque
import uuid
import shutil


class RecordingState(Enum):
    """Recording state enumeration."""
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"
    STOPPING = "stopping"


class StreamManager:
    """
    Manages audio streams with proper lifecycle management.
    """
    
    def __init__(self):
        self.active_streams: Dict[str, pyaudio.Stream] = {}
        self.stream_lock = threading.Lock()
        self.logger = logging.getLogger(f"{__name__}.StreamManager")
    
    def register_stream(self, stream_id: str, stream: pyaudio.Stream):
        """Register a new stream."""
        with self.stream_lock:
            self.active_streams[stream_id] = stream
            self.logger.debug(f"Registered stream {stream_id}")
    
    def close_stream(self, stream_id: str) -> bool:
        """Close and unregister a stream."""
        with self.stream_lock:
            stream = self.active_streams.get(stream_id)
            if stream:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
                    del self.active_streams[stream_id]
                    self.logger.debug(f"Closed stream {stream_id}")
                    return True
                except Exception as e:
                    self.logger.error(f"Error closing stream {stream_id}: {e}")
                    return False
            return False
    
    def close_all_streams(self):
        """Close all active streams."""
        with self.stream_lock:
            stream_ids = list(self.active_streams.keys())
            for stream_id in stream_ids:
                self.close_stream(stream_id)
    
    def get_stream(self, stream_id: str) -> Optional[pyaudio.Stream]:
        """Get stream by ID."""
        with self.stream_lock:
            return self.active_streams.get(stream_id)


class RecordingSession:
    """
    Represents an active recording session.
    """
    
    def __init__(self, channel: int, stream_id: str, file_path: str):
        self.channel = channel
        self.stream_id = stream_id
        self.file_path = file_path
        self.temp_file_path = file_path
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self.frames: List[bytes] = []
        self.state = RecordingState.RECORDING
        self.stop_event = threading.Event()
        self.recording_thread: Optional[threading.Thread] = None
        self.bytes_recorded = 0
        self.error_message: Optional[str] = None
    
    def get_duration(self) -> float:
        """Get recording duration in seconds."""
        end_time = self.end_time or datetime.now()
        return (end_time - self.start_time).total_seconds()
    
    def add_frame(self, frame: bytes):
        """Add audio frame to recording."""
        self.frames.append(frame)
        self.bytes_recorded += len(frame)
    
    def set_error(self, error_message: str):
        """Set error state."""
        self.state = RecordingState.ERROR
        self.error_message = error_message
        self.stop_event.set()
    
    def stop(self):
        """Signal recording to stop."""
        self.state = RecordingState.STOPPING
        self.stop_event.set()
        self.end_time = datetime.now()
    
    def get_metadata(self) -> Dict:
        """Get recording metadata."""
        return {
            'channel': self.channel,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration': self.get_duration(),
            'bytes_recorded': self.bytes_recorded,
            'frame_count': len(self.frames),
            'state': self.state.value,
            'file_path': self.file_path,
            'error_message': self.error_message
        }


class AudioRecorder:
    """
    Audio recorder for capturing audio from USB microphone.
    
    Supports channel-based recording with proper file management,
    GPIO integration, and speech processing pipeline integration.
    """
    
    def __init__(self, audio_device_manager, config: Dict, file_manager=None):
        """
        Initialize audio recorder.
        
        Args:
            audio_device_manager: AudioDeviceManager instance
            config: Configuration dictionary
            file_manager: Optional FileManager instance
        """
        self.audio_device_manager = audio_device_manager
        self.config = config
        self.file_manager = file_manager
        self.logger = logging.getLogger(__name__)
        
        # Audio configuration
        self.audio_config = config.get('audio', {})
        self.sample_rate = self.audio_config.get('sample_rate', 44100)
        self.chunk_size = self.audio_config.get('chunk_size', 1024)
        self.format = getattr(pyaudio, self.audio_config.get('format', 'paInt16'))
        self.channels = self.audio_config.get('channels', 1)
        self.max_recording_duration = self.audio_config.get('max_recording_duration', 300)
        self.min_recording_duration = self.audio_config.get('min_recording_duration', 1.0)
        
        # File management
        self.paths = config.get('paths', {})
        self.recordings_dir = self.paths.get('recordings', './recordings')
        self.temp_dir = self.paths.get('temp', './temp')
        self.playable_dir = self.paths.get('playable', './playable')
        self.bin_dir = self.paths.get('bin', './bin')
        
        # Stream management
        self.stream_manager = StreamManager()
        
        # Recording state management
        self.active_sessions: Dict[int, RecordingSession] = {}  # channel -> session
        self.recording_lock = threading.Lock()
        self.channel_states: Dict[int, RecordingState] = {}
        
        # Initialize all channels as idle
        for channel in range(1, 6):
            self.channel_states[channel] = RecordingState.IDLE
        
        # Callbacks and integration
        self.recording_complete_callbacks: List[Callable] = []
        self.gpio_handler: Optional[object] = None
        
        # Performance tracking
        self.performance_stats = {
            'recordings_started': 0,
            'recordings_completed': 0,
            'recordings_failed': 0,
            'total_recording_time': 0.0,
            'average_recording_duration': 0.0,
            'concurrent_recordings_max': 0,
            'errors': deque(maxlen=100)
        }
        self.stats_lock = threading.Lock()
        
        # System health monitoring
        self.health_check_interval = 30  # seconds
        self.health_monitor_thread: Optional[threading.Thread] = None
        self.stop_health_monitoring = threading.Event()
        
        # Emergency stop capability
        self.emergency_stop_event = threading.Event()
        
        # Ensure directories exist
        self._create_directories()
        
        # Start health monitoring
        self._start_health_monitoring()
    
    def _create_directories(self):
        """Create necessary directories for recordings."""
        try:
            # Main directories
            directories = [self.recordings_dir, self.temp_dir, self.playable_dir, self.bin_dir]
            for directory in directories:
                os.makedirs(directory, exist_ok=True)
            
            # Channel-specific directories
            for channel in range(1, 6):
                channel_playable = os.path.join(self.playable_dir, f"channel_{channel}")
                channel_bin = os.path.join(self.bin_dir, f"channel_{channel}")
                os.makedirs(channel_playable, exist_ok=True)
                os.makedirs(channel_bin, exist_ok=True)
            
            self.logger.info("Created complete recording directory structure")
        except Exception as e:
            self.logger.error(f"Failed to create recording directories: {e}")
            raise
    
    def _start_health_monitoring(self):
        """Start health monitoring thread."""
        if self.health_monitor_thread and self.health_monitor_thread.is_alive():
            return
        
        self.health_monitor_thread = threading.Thread(
            target=self._health_monitor_worker,
            daemon=True,
            name="RecorderHealthMonitor"
        )
        self.health_monitor_thread.start()
        self.logger.info("Started recorder health monitoring")
    
    def _health_monitor_worker(self):
        """Health monitoring worker thread."""
        while not self.stop_health_monitoring.is_set():
            try:
                self._perform_health_check()
                time.sleep(self.health_check_interval)
            except Exception as e:
                self.logger.error(f"Health monitoring error: {e}")
                time.sleep(10)  # Shorter sleep on error
    
    def _perform_health_check(self):
        """Perform system health check."""
        try:
            # Check for stuck recordings
            current_time = datetime.now()
            with self.recording_lock:
                for channel, session in list(self.active_sessions.items()):
                    duration = (current_time - session.start_time).total_seconds()
                    if duration > self.max_recording_duration:
                        self.logger.warning(f"Recording on channel {channel} exceeded max duration, stopping")
                        self._force_stop_recording(channel, "Maximum duration exceeded")
            
            # Check audio device health
            if not self.audio_device_manager.get_input_device():
                self.logger.error("Input device not available during health check")
            
            # Update performance statistics
            self._update_performance_stats()
            
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
    
    def _update_performance_stats(self):
        """Update performance statistics."""
        with self.stats_lock:
            # Update concurrent recordings max
            current_concurrent = len(self.active_sessions)
            if current_concurrent > self.performance_stats['concurrent_recordings_max']:
                self.performance_stats['concurrent_recordings_max'] = current_concurrent
            
            # Update average recording duration
            if self.performance_stats['recordings_completed'] > 0:
                self.performance_stats['average_recording_duration'] = (
                    self.performance_stats['total_recording_time'] / 
                    self.performance_stats['recordings_completed']
                )
    
    def set_gpio_handler(self, gpio_handler):
        """
        Set GPIO handler for button integration.
        
        Args:
            gpio_handler: GPIOHandler instance for button callbacks
        """
        self.gpio_handler = gpio_handler
        if gpio_handler:
            # Register recording callbacks for all channels
            for channel in range(1, 6):
                gpio_handler.register_recording_callback(
                    channel, self._gpio_recording_callback
                )
            self.logger.info("GPIO handler registered with recording callbacks")
    
    def _gpio_recording_callback(self, channel: int, action: str):
        """
        Handle GPIO button callbacks.
        
        Args:
            channel: Button channel (1-5)
            action: Action type ("start_recording", "stop_recording", "emergency_stop")
        """
        try:
            if action == "start_recording":
                success = self.start_recording(channel)
                if not success:
                    self.logger.warning(f"Failed to start recording on channel {channel} from GPIO")
            elif action == "stop_recording":
                file_path = self.stop_recording(channel)
                if not file_path:
                    self.logger.warning(f"Failed to stop recording on channel {channel} from GPIO")
            elif action == "emergency_stop":
                self._force_stop_recording(channel, "Emergency stop via GPIO")
            else:
                self.logger.warning(f"Unknown GPIO action: {action}")
        except Exception as e:
            self.logger.error(f"GPIO callback error for channel {channel}: {e}")
    
    def register_completion_callback(self, callback: Callable):
        """
        Register callback function for recording completion.
        
        Args:
            callback: Function to call when recording is complete
                     Signature: callback(channel, file_path, metadata)
        """
        self.recording_complete_callbacks.append(callback)
        self.logger.info("Recording completion callback registered")
    
    def set_recording_complete_callback(self, callback: Callable):
        """Legacy method - use register_completion_callback instead."""
        self.register_completion_callback(callback)
    
    def start_recording(self, channel: int) -> bool:
        """
        Start recording on specified channel.
        
        Args:
            channel: Recording channel (1-5)
            
        Returns:
            True if recording started successfully, False otherwise
        """
        # Validate channel
        if not (1 <= channel <= 5):
            self.logger.error(f"Invalid channel: {channel}. Must be 1-5")
            return False
        
        # Check emergency stop
        if self.emergency_stop_event.is_set():
            self.logger.warning("Cannot start recording: emergency stop is active")
            return False
        
        with self.recording_lock:
            # Check if already recording on this channel
            if channel in self.active_sessions:
                self.logger.warning(f"Recording already active on channel {channel}")
                return False
            
            # Check if channel is in error state
            if self.channel_states.get(channel) == RecordingState.ERROR:
                self.logger.warning(f"Channel {channel} is in error state, cannot start recording")
                return False
            
            try:
                # Get input device
                input_device = self.audio_device_manager.get_input_device()
                if not input_device:
                    self.logger.error("No input device available for recording")
                    return False
                
                # Create audio stream
                stream_id = f"recording_ch{channel}_{uuid.uuid4().hex[:8]}"
                stream = self.audio_device_manager.create_input_stream(stream_id)
                if not stream:
                    self.logger.error("Failed to create input stream")
                    return False
                
                # Register stream
                self.stream_manager.register_stream(stream_id, stream)
                
                # Generate temporary file path
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"recording_ch{channel}_{timestamp}_{uuid.uuid4().hex[:8]}.wav"
                temp_file_path = os.path.join(self.temp_dir, filename)
                
                # Create recording session
                session = RecordingSession(channel, stream_id, temp_file_path)
                
                # Start recording thread
                recording_thread = threading.Thread(
                    target=self._recording_worker,
                    args=(session,),
                    daemon=True,
                    name=f"RecordingWorker-Ch{channel}"
                )
                session.recording_thread = recording_thread
                
                # Update state
                self.active_sessions[channel] = session
                self.channel_states[channel] = RecordingState.RECORDING
                
                # Start recording
                recording_thread.start()
                
                # Update statistics
                with self.stats_lock:
                    self.performance_stats['recordings_started'] += 1
                
                self.logger.info(f"Started recording on channel {channel}: {temp_file_path}")
                return True
                
            except Exception as e:
                self.logger.error(f"Failed to start recording on channel {channel}: {e}")
                
                # Clean up on failure
                self.channel_states[channel] = RecordingState.ERROR
                if channel in self.active_sessions:
                    del self.active_sessions[channel]
                
                with self.stats_lock:
                    self.performance_stats['recordings_failed'] += 1
                    self.performance_stats['errors'].append({
                        'timestamp': datetime.now().isoformat(),
                        'channel': channel,
                        'error': str(e),
                        'operation': 'start_recording'
                    })
                
                return False
    
    def stop_recording(self, channel: int) -> Optional[str]:
        """
        Stop recording on specified channel.
        
        Args:
            channel: Recording channel (1-5)
            
        Returns:
            Path to recorded file, or None if recording wasn't active
        """
        # Validate channel
        if not (1 <= channel <= 5):
            self.logger.error(f"Invalid channel: {channel}. Must be 1-5")
            return None
        
        with self.recording_lock:
            session = self.active_sessions.get(channel)
            if not session:
                self.logger.warning(f"No active recording on channel {channel}")
                return None
            
            try:
                self.logger.info(f"Stopping recording on channel {channel}")
                
                # Signal recording to stop
                session.stop()
                self.channel_states[channel] = RecordingState.PROCESSING
                
                # Wait for recording thread to finish
                if session.recording_thread and session.recording_thread.is_alive():
                    session.recording_thread.join(timeout=10.0)
                    if session.recording_thread.is_alive():
                        self.logger.warning(f"Recording thread for channel {channel} did not stop gracefully")
                
                # Close stream
                self.stream_manager.close_stream(session.stream_id)
                
                # Check minimum duration
                duration = session.get_duration()
                if duration < self.min_recording_duration:
                    self.logger.info(f"Recording on channel {channel} too short ({duration:.2f}s), discarding")
                    self._cleanup_session(channel, session)
                    return None
                
                # Save audio file
                final_file_path = self._save_recording(session)
                
                # Clean up session
                self._cleanup_session(channel, session)
                
                if final_file_path:
                    # Update statistics
                    with self.stats_lock:
                        self.performance_stats['recordings_completed'] += 1
                        self.performance_stats['total_recording_time'] += duration
                    
                    # Call completion callbacks
                    self._call_completion_callbacks(channel, final_file_path, session.get_metadata())
                    
                    self.logger.info(f"Successfully stopped recording on channel {channel}: {final_file_path}")
                    return final_file_path
                else:
                    self.logger.error(f"Failed to save recording for channel {channel}")
                    with self.stats_lock:
                        self.performance_stats['recordings_failed'] += 1
                    return None
                
            except Exception as e:
                self.logger.error(f"Failed to stop recording on channel {channel}: {e}")
                
                # Clean up on error
                self._cleanup_session(channel, session, error=True)
                
                with self.stats_lock:
                    self.performance_stats['recordings_failed'] += 1
                    self.performance_stats['errors'].append({
                        'timestamp': datetime.now().isoformat(),
                        'channel': channel,
                        'error': str(e),
                        'operation': 'stop_recording'
                    })
                
                return None
    
    def _recording_worker(self, session: RecordingSession):
        """
        Worker thread for audio recording.
        
        Args:
            session: RecordingSession instance
        """
        channel = session.channel
        stream = self.stream_manager.get_stream(session.stream_id)
        
        if not stream:
            self.logger.error(f"No stream found for channel {channel}")
            session.set_error("Stream not found")
            return
        
        self.logger.debug(f"Recording worker started for channel {channel}")
        
        try:
            # Start the stream if not already active
            if not stream.is_active():
                stream.start_stream()
            
            while not session.stop_event.is_set() and not self.emergency_stop_event.is_set():
                try:
                    # Read audio data with timeout
                    if stream.is_active():
                        data = stream.read(self.chunk_size, exception_on_overflow=False)
                        if data:
                            session.add_frame(data)
                    else:
                        self.logger.warning(f"Stream not active for channel {channel}")
                        break
                    
                    # Check for maximum duration (safety limit)
                    duration = session.get_duration()
                    if duration > self.max_recording_duration:
                        self.logger.warning(f"Recording on channel {channel} exceeded maximum duration ({duration:.2f}s)")
                        break
                    
                    # Small sleep to prevent busy waiting
                    time.sleep(0.001)  # 1ms
                    
                except Exception as e:
                    self.logger.error(f"Error reading audio data on channel {channel}: {e}")
                    session.set_error(f"Audio read error: {e}")
                    break
            
            # Stop the stream
            if stream.is_active():
                stream.stop_stream()
            
        except Exception as e:
            self.logger.error(f"Recording worker error on channel {channel}: {e}")
            session.set_error(f"Worker error: {e}")
        finally:
            session.state = RecordingState.STOPPING
            self.logger.debug(f"Recording worker finished for channel {channel}")
    
    def _cleanup_session(self, channel: int, session: RecordingSession, error: bool = False):
        """
        Clean up recording session.
        
        Args:
            channel: Recording channel
            session: RecordingSession to clean up
            error: Whether cleanup is due to error
        """
        try:
            # Remove from active sessions
            if channel in self.active_sessions:
                del self.active_sessions[channel]
            
            # Update channel state
            if error:
                self.channel_states[channel] = RecordingState.ERROR
            else:
                self.channel_states[channel] = RecordingState.IDLE
            
            # Close stream if still active
            self.stream_manager.close_stream(session.stream_id)
            
            # Delete temp file if it exists and was not processed
            if error and os.path.exists(session.temp_file_path):
                try:
                    os.remove(session.temp_file_path)
                    self.logger.debug(f"Deleted temp file: {session.temp_file_path}")
                except Exception as e:
                    self.logger.warning(f"Could not delete temp file {session.temp_file_path}: {e}")
            
        except Exception as e:
            self.logger.error(f"Error during session cleanup for channel {channel}: {e}")
    
    def _call_completion_callbacks(self, channel: int, file_path: str, metadata: Dict):
        """
        Call all registered completion callbacks.
        
        Args:
            channel: Recording channel
            file_path: Path to completed recording
            metadata: Recording metadata
        """
        for callback in self.recording_complete_callbacks:
            try:
                # Call callback in separate thread to avoid blocking
                threading.Thread(
                    target=callback,
                    args=(channel, file_path, metadata),
                    daemon=True,
                    name=f"CompletionCallback-Ch{channel}"
                ).start()
            except Exception as e:
                self.logger.error(f"Error calling completion callback: {e}")
    
    def _save_recording(self, session: RecordingSession) -> Optional[str]:
        """
        Save recorded audio frames to WAV file.
        
        Args:
            session: RecordingSession with audio data
            
        Returns:
            Path to saved file, or None if save failed
        """
        try:
            if not session.frames:
                self.logger.warning(f"No audio data to save for channel {session.channel}")
                return None
            
            # Generate final filename
            timestamp = session.start_time.strftime("%Y%m%d_%H%M%S")
            final_filename = f"{timestamp}_recording.wav"
            
            # Determine final directory based on channel
            recordings_channel_dir = os.path.join(self.recordings_dir, f"channel_{session.channel}")
            os.makedirs(recordings_channel_dir, exist_ok=True)
            
            final_file_path = os.path.join(recordings_channel_dir, final_filename)
            
            # Ensure unique filename
            counter = 1
            while os.path.exists(final_file_path):
                base_name = f"{timestamp}_recording_{counter}.wav"
                final_file_path = os.path.join(recordings_channel_dir, base_name)
                counter += 1
            
            # Write WAV file
            with wave.open(final_file_path, 'wb') as wav_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(pyaudio.get_sample_size(self.format))
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(b''.join(session.frames))
            
            # Verify file was created and has content
            if os.path.exists(final_file_path) and os.path.getsize(final_file_path) > 0:
                file_size = os.path.getsize(final_file_path)
                duration = session.get_duration()
                
                self.logger.info(
                    f"Successfully saved recording for channel {session.channel}: "
                    f"{final_file_path} ({file_size} bytes, {duration:.2f}s)"
                )
                
                # Update session with final path
                session.file_path = final_file_path
                
                return final_file_path
            else:
                self.logger.error(f"Recording file is empty or wasn't created: {final_file_path}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to save recording for channel {session.channel}: {e}")
            return None
    
    def _force_stop_recording(self, channel: int, reason: str):
        """
        Force stop recording on channel (for emergency situations).
        
        Args:
            channel: Channel to stop
            reason: Reason for force stop
        """
        with self.recording_lock:
            session = self.active_sessions.get(channel)
            if session:
                self.logger.warning(f"Force stopping recording on channel {channel}: {reason}")
                session.set_error(f"Force stopped: {reason}")
                
                # Force close stream
                self.stream_manager.close_stream(session.stream_id)
                
                # Clean up
                self._cleanup_session(channel, session, error=True)
    
    def emergency_stop_all(self):
        """Emergency stop all active recordings."""
        self.logger.warning("Emergency stop all recordings activated")
        self.emergency_stop_event.set()
        
        with self.recording_lock:
            active_channels = list(self.active_sessions.keys())
        
        for channel in active_channels:
            self._force_stop_recording(channel, "Emergency stop all")
        
        # Close all streams
        self.stream_manager.close_all_streams()
        
        self.logger.warning(f"Emergency stopped {len(active_channels)} active recordings")
    
    def clear_emergency_stop(self):
        """Clear emergency stop state."""
        self.emergency_stop_event.clear()
        self.logger.info("Emergency stop cleared - recordings can resume")
    
    def is_recording(self, channel: int) -> bool:
        """
        Check if recording is active on specified channel.
        
        Args:
            channel: Channel to check (1-5)
            
        Returns:
            True if recording is active on channel
        """
        with self.recording_lock:
            return channel in self.active_sessions
    
    def get_active_recordings(self) -> Dict[int, Dict]:
        """
        Get information about all active recordings.
        
        Returns:
            Dictionary mapping channels to recording information
        """
        with self.recording_lock:
            active_info = {}
            for channel, session in self.active_sessions.items():
                active_info[channel] = {
                    'channel': channel,
                    'start_time': session.start_time.isoformat(),
                    'duration': session.get_duration(),
                    'file_path': session.file_path,
                    'state': session.state.value,
                    'bytes_recorded': session.bytes_recorded,
                    'stream_id': session.stream_id
                }
            return active_info
    
    def stop_all_recordings(self):
        """Stop all active recordings."""
        with self.recording_lock:
            active_channels = list(self.active_sessions.keys())
        
        for channel in active_channels:
            self.stop_recording(channel)
        
        self.logger.info(f"Stopped {len(active_channels)} active recordings")
    
    # Required interface methods for subsequent agents
    
    def get_completed_recordings(self, channel: Optional[int] = None) -> List[Dict]:
        """
        Get completed recordings for Speech Processor integration.
        
        Args:
            channel: Optional channel filter (1-5)
            
        Returns:
            List of completed recording dictionaries
        """
        try:
            completed_recordings = []
            
            # Search in recordings directory
            search_dir = self.recordings_dir
            
            if channel:
                # Search specific channel
                channel_dir = os.path.join(search_dir, f"channel_{channel}")
                if os.path.exists(channel_dir):
                    for file_path in glob.glob(os.path.join(channel_dir, "*.wav")):
                        metadata = self.get_recording_metadata(file_path)
                        if metadata:
                            completed_recordings.append(metadata)
            else:
                # Search all channels
                for ch in range(1, 6):
                    channel_dir = os.path.join(search_dir, f"channel_{ch}")
                    if os.path.exists(channel_dir):
                        for file_path in glob.glob(os.path.join(channel_dir, "*.wav")):
                            metadata = self.get_recording_metadata(file_path)
                            if metadata:
                                completed_recordings.append(metadata)
            
            # Sort by creation time (newest first)
            completed_recordings.sort(key=lambda x: x.get('created_time', ''), reverse=True)
            
            return completed_recordings
            
        except Exception as e:
            self.logger.error(f"Error getting completed recordings: {e}")
            return []
    
    def get_recording_metadata(self, file_path: str) -> Optional[Dict]:
        """
        Get metadata for a specific recording file.
        
        Args:
            file_path: Path to recording file
            
        Returns:
            Recording metadata dictionary or None
        """
        try:
            if not os.path.exists(file_path):
                return None
            
            # Basic file information
            stat = os.stat(file_path)
            
            # Extract channel from path
            channel = None
            for ch in range(1, 6):
                if f"channel_{ch}" in file_path:
                    channel = ch
                    break
            
            # Audio file information
            audio_info = {}
            try:
                with wave.open(file_path, 'rb') as wav_file:
                    audio_info = {
                        'duration': wav_file.getnframes() / wav_file.getframerate(),
                        'sample_rate': wav_file.getframerate(),
                        'channels': wav_file.getnchannels(),
                        'sample_width': wav_file.getsampwidth(),
                        'frames': wav_file.getnframes()
                    }
            except Exception:
                pass
            
            metadata = {
                'file_path': file_path,
                'filename': os.path.basename(file_path),
                'channel': channel,
                'size_bytes': stat.st_size,
                'created_time': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                'modified_time': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                **audio_info
            }
            
            return metadata
            
        except Exception as e:
            self.logger.error(f"Error getting metadata for {file_path}: {e}")
            return None
    
    def get_recording_status(self, channel: int) -> Dict:
        """
        Get recording status for Main Controller integration.
        
        Args:
            channel: Channel number (1-5)
            
        Returns:
            Status dictionary
        """
        try:
            with self.recording_lock:
                session = self.active_sessions.get(channel)
                channel_state = self.channel_states.get(channel, RecordingState.IDLE)
                
                status = {
                    'channel': channel,
                    'state': channel_state.value,
                    'is_recording': channel in self.active_sessions,
                    'session_info': None
                }
                
                if session:
                    status['session_info'] = {
                        'start_time': session.start_time.isoformat(),
                        'duration': session.get_duration(),
                        'bytes_recorded': session.bytes_recorded,
                        'frames_count': len(session.frames),
                        'file_path': session.file_path,
                        'stream_id': session.stream_id,
                        'error_message': session.error_message
                    }
                
                return status
                
        except Exception as e:
            self.logger.error(f"Error getting recording status for channel {channel}: {e}")
            return {
                'channel': channel,
                'state': 'error',
                'is_recording': False,
                'error': str(e)
            }
    
    def get_system_status(self) -> Dict:
        """
        Get overall system status.
        
        Returns:
            System status dictionary
        """
        try:
            with self.recording_lock:
                active_count = len(self.active_sessions)
                
            with self.stats_lock:
                stats = self.performance_stats.copy()
            
            # Get channel states
            channel_states = {}
            for channel in range(1, 6):
                channel_states[f'channel_{channel}'] = self.channel_states.get(channel, RecordingState.IDLE).value
            
            status = {
                'timestamp': datetime.now().isoformat(),
                'active_recordings': active_count,
                'emergency_stop_active': self.emergency_stop_event.is_set(),
                'channel_states': channel_states,
                'performance_stats': stats,
                'system_health': {
                    'audio_device_available': bool(self.audio_device_manager.get_input_device()),
                    'directories_created': all(os.path.exists(d) for d in [
                        self.recordings_dir, self.temp_dir, self.playable_dir, self.bin_dir
                    ]),
                    'stream_manager_active': bool(self.stream_manager),
                    'health_monitoring_active': self.health_monitor_thread and self.health_monitor_thread.is_alive()
                }
            }
            
            return status
            
        except Exception as e:
            self.logger.error(f"Error getting system status: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'system_health': {'status': 'error'}
            }
    
    def get_performance_stats(self) -> Dict:
        """Get performance statistics."""
        with self.stats_lock:
            stats = self.performance_stats.copy()
            
        # Add current active sessions info
        with self.recording_lock:
            stats['current_active_sessions'] = len(self.active_sessions)
            stats['active_channels'] = list(self.active_sessions.keys())
        
        return stats
    
    def cleanup(self):
        """Clean up recorder resources."""
        try:
            self.logger.info("Starting audio recorder cleanup")
            
            # Stop health monitoring
            self.stop_health_monitoring.set()
            if self.health_monitor_thread and self.health_monitor_thread.is_alive():
                self.health_monitor_thread.join(timeout=5.0)
            
            # Stop all recordings
            self.stop_all_recordings()
            
            # Close all streams
            self.stream_manager.close_all_streams()
            
            # Clear states
            with self.recording_lock:
                self.active_sessions.clear()
                for channel in range(1, 6):
                    self.channel_states[channel] = RecordingState.IDLE
            
            self.logger.info("Audio recorder cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Audio recorder cleanup failed: {e}")


# Import for compatibility
import glob