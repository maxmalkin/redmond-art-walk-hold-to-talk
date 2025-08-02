"""
Audio Output Manager for Raspberry Pi Audio System.

Manages multi-channel audio playback with proper channel mapping
and concurrent playback support for all 5 output channels.
"""

import threading
import time
import logging
import wave
import os
from typing import Dict, Optional, List, Callable
import pyaudio
from datetime import datetime


class PlaybackSession:
    """Represents an active playback session."""
    
    def __init__(self, channel: int, file_path: str, stream: pyaudio.Stream):
        self.channel = channel
        self.file_path = file_path
        self.stream = stream
        self.start_time = datetime.now()
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.is_playing = False
        self.completion_callback: Optional[Callable] = None


class AudioOutputManager:
    """
    Audio output manager for multi-channel playback system.
    
    Handles independent playback on 5 audio output channels with
    proper channel mapping and concurrent operation support.
    """
    
    def __init__(self, audio_device_manager, content_filter, config: Dict):
        """
        Initialize audio output manager.
        
        Args:
            audio_device_manager: AudioDeviceManager instance
            content_filter: ContentFilter instance for file access
            config: Configuration dictionary
        """
        self.audio_device_manager = audio_device_manager
        self.content_filter = content_filter
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Audio configuration
        self.audio_config = config.get('audio', {})
        self.chunk_size = self.audio_config.get('chunk_size', 1024)
        
        # Playback sessions (one per channel)
        self.active_sessions: Dict[int, PlaybackSession] = {}
        self.sessions_lock = threading.Lock()
        
        # Playback statistics
        self.playback_stats = {
            'total_playbacks': 0,
            'successful_playbacks': 0,
            'failed_playbacks': 0,
            'concurrent_playbacks': 0
        }
        self.stats_lock = threading.Lock()
        
        # Callbacks
        self.playback_callbacks: List[Callable] = []
    
    def add_playback_callback(self, callback: Callable):
        """
        Add callback for playback events.
        
        Args:
            callback: Function to call on playback events
                     Signature: callback(event, channel, file_path, details)
        """
        self.playback_callbacks.append(callback)
        self.logger.info("Added playback callback")
    
    def trigger_playback(self, channel: int) -> bool:
        """
        Trigger playback on specified channel.
        
        Plays the most recent clean audio file for the channel.
        Stops any currently playing audio on the same channel.
        
        Args:
            channel: Playback channel (1-5)
            
        Returns:
            True if playback started successfully
        """
        try:
            self.logger.info(f"Triggering playback on channel {channel}")
            
            # Stop any existing playback on this channel
            self.stop_playback(channel)
            
            # Get latest playable file for channel
            file_path = self.content_filter.get_latest_playable_file(channel)
            if not file_path:
                self.logger.warning(f"No playable files available for channel {channel}")
                self._call_playback_callbacks("no_file", channel, None, {"reason": "No playable files"})
                return False
            
            # Start playback
            return self._start_playback(channel, file_path)
            
        except Exception as e:
            self.logger.error(f"Failed to trigger playback on channel {channel}: {e}")
            self._update_stats('failed_playbacks')
            return False
    
    def play_file(self, channel: int, file_path: str) -> bool:
        """
        Play specific file on channel.
        
        Args:
            channel: Playback channel (1-5)
            file_path: Path to audio file to play
            
        Returns:
            True if playback started successfully
        """
        try:
            if not os.path.exists(file_path):
                self.logger.error(f"Audio file not found: {file_path}")
                return False
            
            self.logger.info(f"Playing file on channel {channel}: {file_path}")
            
            # Stop any existing playback on this channel
            self.stop_playback(channel)
            
            # Start playback
            return self._start_playback(channel, file_path)
            
        except Exception as e:
            self.logger.error(f"Failed to play file on channel {channel}: {e}")
            self._update_stats('failed_playbacks')
            return False
    
    def _start_playback(self, channel: int, file_path: str) -> bool:
        """
        Start playback session for channel and file.
        
        Args:
            channel: Playback channel (1-5)
            file_path: Path to audio file
            
        Returns:
            True if playback started successfully
        """
        try:
            # Get output device for channel
            output_device = self.audio_device_manager.get_output_device(channel)
            if not output_device:
                self.logger.error(f"No output device available for channel {channel}")
                return False
            
            # Open and validate audio file
            try:
                wav_file = wave.open(file_path, 'rb')
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                wav_file.close()
            except Exception as e:
                self.logger.error(f"Invalid audio file {file_path}: {e}")
                return False
            
            # Create output stream
            try:
                format_map = {1: pyaudio.paInt8, 2: pyaudio.paInt16, 3: pyaudio.paInt24, 4: pyaudio.paInt32}
                audio_format = format_map.get(sample_width, pyaudio.paInt16)
                
                stream = self.audio_device_manager.pyaudio_instance.open(
                    format=audio_format,
                    channels=channels,
                    rate=sample_rate,
                    output=True,
                    output_device_index=output_device.device_id,
                    frames_per_buffer=self.chunk_size
                )
            except Exception as e:
                self.logger.error(f"Failed to create output stream for channel {channel}: {e}")
                return False
            
            # Create playback session
            session = PlaybackSession(channel, file_path, stream)
            
            # Start playback thread
            playback_thread = threading.Thread(
                target=self._playback_worker,
                args=(session,),
                daemon=True,
                name=f"Playback-Channel-{channel}"
            )
            session.thread = playback_thread
            
            # Register session
            with self.sessions_lock:
                self.active_sessions[channel] = session
            
            # Start playback
            playback_thread.start()
            session.is_playing = True
            
            # Update statistics
            self._update_stats('total_playbacks')
            with self.sessions_lock:
                concurrent = len([s for s in self.active_sessions.values() if s.is_playing])
                with self.stats_lock:
                    self.playback_stats['concurrent_playbacks'] = max(
                        self.playback_stats['concurrent_playbacks'], concurrent
                    )
            
            # Call playback callbacks
            self._call_playback_callbacks("started", channel, file_path, {
                "sample_rate": sample_rate,
                "channels": channels,
                "duration": self._get_audio_duration(file_path)
            })
            
            self.logger.info(f"Started playback on channel {channel}: {os.path.basename(file_path)}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start playback on channel {channel}: {e}")
            return False
    
    def _playback_worker(self, session: PlaybackSession):
        """
        Worker thread for audio playback.
        
        Args:
            session: PlaybackSession to execute
        """
        channel = session.channel
        file_path = session.file_path
        stream = session.stream
        
        try:
            # Open audio file
            with wave.open(file_path, 'rb') as wav_file:
                # Read and play audio data
                while not session.stop_event.is_set():
                    data = wav_file.readframes(self.chunk_size)
                    if not data:
                        break  # End of file
                    
                    try:
                        stream.write(data)
                    except Exception as e:
                        self.logger.error(f"Error writing audio data to channel {channel}: {e}")
                        break
            
            # Playback completed successfully
            self._update_stats('successful_playbacks')
            self._call_playback_callbacks("completed", channel, file_path, {
                "duration": (datetime.now() - session.start_time).total_seconds()
            })
            
            self.logger.info(f"Completed playback on channel {channel}: {os.path.basename(file_path)}")
            
        except Exception as e:
            self.logger.error(f"Playback worker error on channel {channel}: {e}")
            self._update_stats('failed_playbacks')
            self._call_playback_callbacks("failed", channel, file_path, {"error": str(e)})
        
        finally:
            # Clean up session
            try:
                session.is_playing = False
                stream.stop()
                stream.close()
                
                with self.sessions_lock:
                    if channel in self.active_sessions:
                        del self.active_sessions[channel]
                
            except Exception as e:
                self.logger.error(f"Error cleaning up playback session for channel {channel}: {e}")
    
    def stop_playback(self, channel: int) -> bool:
        """
        Stop playback on specified channel.
        
        Args:
            channel: Channel to stop (1-5)
            
        Returns:
            True if playback was stopped
        """
        try:
            with self.sessions_lock:
                session = self.active_sessions.get(channel)
                if not session:
                    return False
            
            # Signal stop and wait for thread
            session.stop_event.set()
            if session.thread:
                session.thread.join(timeout=2.0)
            
            self.logger.info(f"Stopped playback on channel {channel}")
            self._call_playback_callbacks("stopped", channel, session.file_path, {})
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to stop playback on channel {channel}: {e}")
            return False
    
    def stop_all_playback(self):
        """Stop playback on all channels."""
        with self.sessions_lock:
            active_channels = list(self.active_sessions.keys())
        
        for channel in active_channels:
            self.stop_playback(channel)
        
        self.logger.info("Stopped all playback")
    
    def is_playing(self, channel: int) -> bool:
        """
        Check if audio is currently playing on channel.
        
        Args:
            channel: Channel to check (1-5)
            
        Returns:
            True if audio is playing on channel
        """
        with self.sessions_lock:
            session = self.active_sessions.get(channel)
            return session.is_playing if session else False
    
    def get_active_playback_info(self) -> Dict[int, Dict]:
        """
        Get information about active playback sessions.
        
        Returns:
            Dictionary mapping channels to playback information
        """
        with self.sessions_lock:
            info = {}
            for channel, session in self.active_sessions.items():
                if session.is_playing:
                    info[channel] = {
                        'file_path': session.file_path,
                        'file_name': os.path.basename(session.file_path),
                        'start_time': session.start_time.isoformat(),
                        'duration': (datetime.now() - session.start_time).total_seconds()
                    }
            return info
    
    def get_available_files(self, channel: int) -> List[Dict]:
        """
        Get list of available files for playback on channel.
        
        Args:
            channel: Channel number (1-5)
            
        Returns:
            List of file information dictionaries
        """
        try:
            files = self.content_filter.get_channel_files(channel, clean_only=True)
            file_info = []
            
            for file_path in files:
                try:
                    stat = os.stat(file_path)
                    duration = self._get_audio_duration(file_path)
                    
                    file_info.append({
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'size': stat.st_size,
                        'modified_time': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        'duration': duration
                    })
                except Exception as e:
                    self.logger.warning(f"Error getting info for file {file_path}: {e}")
            
            return file_info
            
        except Exception as e:
            self.logger.error(f"Failed to get available files for channel {channel}: {e}")
            return []
    
    def _get_audio_duration(self, file_path: str) -> Optional[float]:
        """
        Get duration of audio file in seconds.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Duration in seconds, or None if unable to determine
        """
        try:
            with wave.open(file_path, 'rb') as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                return frames / float(rate)
        except Exception:
            return None
    
    def _call_playback_callbacks(self, event: str, channel: int, file_path: Optional[str], details: Dict):
        """Call registered playback callbacks."""
        for callback in self.playback_callbacks:
            try:
                callback(event, channel, file_path, details)
            except Exception as e:
                self.logger.error(f"Error in playback callback: {e}")
    
    def _update_stats(self, stat_name: str):
        """Update playback statistics."""
        with self.stats_lock:
            self.playback_stats[stat_name] += 1
    
    def get_playback_stats(self) -> Dict:
        """
        Get playback statistics.
        
        Returns:
            Dictionary with playback statistics
        """
        with self.stats_lock:
            stats = self.playback_stats.copy()
        
        # Add current active sessions
        with self.sessions_lock:
            stats['active_sessions'] = len(self.active_sessions)
            stats['channels_playing'] = [
                channel for channel, session in self.active_sessions.items()
                if session.is_playing
            ]
        
        return stats
    
    def test_channel_output(self, channel: int, duration: float = 1.0) -> bool:
        """
        Test audio output on specified channel.
        
        Args:
            channel: Channel to test (1-5)
            duration: Test tone duration in seconds
            
        Returns:
            True if test was successful
        """
        try:
            output_device = self.audio_device_manager.get_output_device(channel)
            if not output_device:
                self.logger.error(f"No output device for channel {channel}")
                return False
            
            # Generate simple test tone (440Hz sine wave)
            import math
            import numpy as np
            
            sample_rate = 44100
            frequency = 440  # A4 note
            samples = int(sample_rate * duration)
            
            # Generate sine wave
            t = np.linspace(0, duration, samples, False)
            tone = np.sin(2 * np.pi * frequency * t) * 0.3  # 30% volume
            tone = (tone * 32767).astype(np.int16)  # Convert to 16-bit
            
            # Create output stream
            stream = self.audio_device_manager.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                output=True,
                output_device_index=output_device.device_id,
                frames_per_buffer=self.chunk_size
            )
            
            # Play test tone
            stream.write(tone.tobytes())
            stream.stop()
            stream.close()
            
            self.logger.info(f"Audio test successful on channel {channel}")
            return True
            
        except Exception as e:
            self.logger.error(f"Audio test failed on channel {channel}: {e}")
            return False
    
    def cleanup(self):
        """Clean up audio output manager resources."""
        try:
            self.stop_all_playback()
            self.logger.info("Audio output manager cleanup completed")
        except Exception as e:
            self.logger.error(f"Audio output manager cleanup failed: {e}")