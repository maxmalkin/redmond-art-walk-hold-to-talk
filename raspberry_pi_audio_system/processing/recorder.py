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
from typing import Optional, Callable, Dict
from datetime import datetime
import pyaudio


class AudioRecorder:
    """
    Audio recorder for capturing audio from USB microphone.
    
    Supports channel-based recording with proper file management
    and integration with the speech processing pipeline.
    """
    
    def __init__(self, audio_device_manager, config: Dict):
        """
        Initialize audio recorder.
        
        Args:
            audio_device_manager: AudioDeviceManager instance
            config: Configuration dictionary
        """
        self.audio_device_manager = audio_device_manager
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Audio configuration
        self.audio_config = config.get('audio', {})
        self.sample_rate = self.audio_config.get('sample_rate', 44100)
        self.chunk_size = self.audio_config.get('chunk_size', 1024)
        self.format = getattr(pyaudio, self.audio_config.get('format', 'paInt16'))
        self.channels = self.audio_config.get('channels', 1)
        
        # File management
        self.recordings_dir = config.get('paths', {}).get('recordings', './recordings')
        self.temp_dir = config.get('paths', {}).get('temp', './temp')
        
        # Recording state
        self.active_recordings: Dict[int, Dict] = {}  # channel -> recording_info
        self.recording_lock = threading.Lock()
        
        # Callbacks
        self.recording_complete_callback: Optional[Callable] = None
        
        # Ensure directories exist
        self._create_directories()
    
    def _create_directories(self):
        """Create necessary directories for recordings."""
        try:
            os.makedirs(self.recordings_dir, exist_ok=True)
            os.makedirs(self.temp_dir, exist_ok=True)
            self.logger.info(f"Created recording directories: {self.recordings_dir}, {self.temp_dir}")
        except Exception as e:
            self.logger.error(f"Failed to create recording directories: {e}")
            raise
    
    def set_recording_complete_callback(self, callback: Callable):
        """
        Set callback function for recording completion.
        
        Args:
            callback: Function to call when recording is complete
                     Signature: callback(channel, file_path, metadata)
        """
        self.recording_complete_callback = callback
        self.logger.info("Recording complete callback registered")
    
    def start_recording(self, channel: int) -> bool:
        """
        Start recording on specified channel.
        
        Args:
            channel: Recording channel (1-5)
            
        Returns:
            True if recording started successfully, False otherwise
        """
        with self.recording_lock:
            if channel in self.active_recordings:
                self.logger.warning(f"Recording already active on channel {channel}")
                return False
            
            try:
                # Get input device and create stream
                input_device = self.audio_device_manager.get_input_device()
                if not input_device:
                    self.logger.error("No input device available for recording")
                    return False
                
                stream = self.audio_device_manager.create_input_stream()
                if not stream:
                    self.logger.error("Failed to create input stream")
                    return False
                
                # Generate file path
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"recording_ch{channel}_{timestamp}.wav"
                file_path = os.path.join(self.temp_dir, filename)
                
                # Create recording info
                recording_info = {
                    'channel': channel,
                    'stream': stream,
                    'file_path': file_path,
                    'start_time': datetime.now(),
                    'frames': [],
                    'thread': None,
                    'stop_event': threading.Event()
                }
                
                # Start recording thread
                recording_thread = threading.Thread(
                    target=self._recording_worker,
                    args=(recording_info,),
                    daemon=True
                )
                recording_info['thread'] = recording_thread
                
                self.active_recordings[channel] = recording_info
                recording_thread.start()
                
                self.logger.info(f"Started recording on channel {channel}: {file_path}")
                return True
                
            except Exception as e:
                self.logger.error(f"Failed to start recording on channel {channel}: {e}")
                return False
    
    def stop_recording(self, channel: int) -> Optional[str]:
        """
        Stop recording on specified channel.
        
        Args:
            channel: Recording channel (1-5)
            
        Returns:
            Path to recorded file, or None if recording wasn't active
        """
        with self.recording_lock:
            recording_info = self.active_recordings.get(channel)
            if not recording_info:
                self.logger.warning(f"No active recording on channel {channel}")
                return None
            
            try:
                # Signal recording thread to stop
                recording_info['stop_event'].set()
                
                # Wait for recording thread to finish
                if recording_info['thread']:
                    recording_info['thread'].join(timeout=5.0)
                
                # Close stream
                if recording_info['stream']:
                    recording_info['stream'].close()
                
                # Save audio file
                file_path = self._save_recording(recording_info)
                
                # Remove from active recordings
                del self.active_recordings[channel]
                
                # Notify completion
                if self.recording_complete_callback and file_path:
                    metadata = {
                        'channel': channel,
                        'duration': (datetime.now() - recording_info['start_time']).total_seconds(),
                        'sample_rate': self.sample_rate,
                        'format': self.format,
                        'channels': self.channels
                    }
                    
                    # Call completion callback in separate thread to avoid blocking
                    threading.Thread(
                        target=self.recording_complete_callback,
                        args=(channel, file_path, metadata),
                        daemon=True
                    ).start()
                
                self.logger.info(f"Stopped recording on channel {channel}: {file_path}")
                return file_path
                
            except Exception as e:
                self.logger.error(f"Failed to stop recording on channel {channel}: {e}")
                # Clean up partial recording
                if channel in self.active_recordings:
                    del self.active_recordings[channel]
                return None
    
    def _recording_worker(self, recording_info: Dict):
        """
        Worker thread for audio recording.
        
        Args:
            recording_info: Recording information dictionary
        """
        stream = recording_info['stream']
        stop_event = recording_info['stop_event']
        frames = recording_info['frames']
        channel = recording_info['channel']
        
        try:
            while not stop_event.is_set():
                try:
                    # Read audio data
                    data = stream.read(self.chunk_size, exception_on_overflow=False)
                    frames.append(data)
                    
                    # Check for very long recordings (safety limit)
                    duration = (datetime.now() - recording_info['start_time']).total_seconds()
                    if duration > self.audio_config.get('max_recording_duration', 300):  # 5 minutes default
                        self.logger.warning(f"Recording on channel {channel} exceeded maximum duration")
                        break
                        
                except Exception as e:
                    self.logger.error(f"Error reading audio data on channel {channel}: {e}")
                    break
            
        except Exception as e:
            self.logger.error(f"Recording worker error on channel {channel}: {e}")
        finally:
            self.logger.debug(f"Recording worker finished for channel {channel}")
    
    def _save_recording(self, recording_info: Dict) -> Optional[str]:
        """
        Save recorded audio frames to WAV file.
        
        Args:
            recording_info: Recording information dictionary
            
        Returns:
            Path to saved file, or None if save failed
        """
        try:
            file_path = recording_info['file_path']
            frames = recording_info['frames']
            
            if not frames:
                self.logger.warning("No audio data to save")
                return None
            
            # Write WAV file
            with wave.open(file_path, 'wb') as wav_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(pyaudio.get_sample_size(self.format))
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(b''.join(frames))
            
            # Verify file was created and has content
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                self.logger.info(f"Successfully saved recording: {file_path}")
                return file_path
            else:
                self.logger.error(f"Recording file is empty or wasn't created: {file_path}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to save recording: {e}")
            return None
    
    def is_recording(self, channel: int) -> bool:
        """
        Check if recording is active on specified channel.
        
        Args:
            channel: Channel to check (1-5)
            
        Returns:
            True if recording is active on channel
        """
        with self.recording_lock:
            return channel in self.active_recordings
    
    def get_active_recordings(self) -> Dict[int, Dict]:
        """
        Get information about all active recordings.
        
        Returns:
            Dictionary mapping channels to recording information
        """
        with self.recording_lock:
            active_info = {}
            for channel, recording_info in self.active_recordings.items():
                active_info[channel] = {
                    'channel': channel,
                    'start_time': recording_info['start_time'],
                    'duration': (datetime.now() - recording_info['start_time']).total_seconds(),
                    'file_path': recording_info['file_path']
                }
            return active_info
    
    def stop_all_recordings(self):
        """Stop all active recordings."""
        with self.recording_lock:
            active_channels = list(self.active_recordings.keys())
        
        for channel in active_channels:
            self.stop_recording(channel)
        
        self.logger.info("Stopped all active recordings")
    
    def cleanup(self):
        """Clean up recorder resources."""
        try:
            self.stop_all_recordings()
            self.logger.info("Audio recorder cleanup completed")
        except Exception as e:
            self.logger.error(f"Audio recorder cleanup failed: {e}")