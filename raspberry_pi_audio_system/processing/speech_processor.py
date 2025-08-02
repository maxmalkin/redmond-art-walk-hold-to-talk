"""
Speech Processor for Raspberry Pi Audio System.

MANDATORY: This module integrates with the spchcat library for speech-to-text processing.
spchcat is the ONLY approved STT solution for this system.

Handles real-time speech-to-text conversion and integration with the content filtering pipeline.
"""

import subprocess
import logging
import os
import threading
import time
from typing import Optional, Callable, Dict, List
from datetime import datetime
import json
import tempfile
import wave


class SpeechProcessor:
    """
    Speech-to-text processor using spchcat library.
    
    MANDATORY: Uses spchcat for all speech recognition tasks.
    Provides real-time STT processing with content filtering integration.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize speech processor with spchcat configuration.
        
        Args:
            config: Configuration dictionary containing spchcat settings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # spchcat configuration
        self.spchcat_config = config.get('spchcat', {})
        self.spchcat_path = self.spchcat_config.get('binary_path', '/usr/local/bin/spchcat')
        self.model_path = self.spchcat_config.get('model_path', '/usr/local/share/spchcat/models')
        self.language = self.spchcat_config.get('language', 'en')
        
        # Processing settings
        self.processing_timeout = self.spchcat_config.get('timeout', 30)
        self.confidence_threshold = self.spchcat_config.get('confidence_threshold', 0.7)
        
        # Raspberry Pi optimization settings
        self.max_concurrent_processing = self.spchcat_config.get('max_concurrent_processing', 1)
        self.process_priority = self.spchcat_config.get('process_priority', 10)
        self.memory_limit_mb = self.spchcat_config.get('memory_limit_mb', 512)
        self.min_audio_duration = self.spchcat_config.get('min_audio_duration', 1.0)
        self.max_audio_duration = self.spchcat_config.get('max_audio_duration', 60.0)
        self.sample_rate_check = self.spchcat_config.get('sample_rate_check', True)
        
        # Concurrent processing control
        self.active_processes = 0
        self.process_lock = threading.Lock()
        
        # File paths
        self.temp_dir = config.get('paths', {}).get('temp', './temp')
        
        # Processing queue and callbacks
        self.processing_callback: Optional[Callable] = None
        self.transcript_complete_callback: Optional[Callable] = None
        self.processing_queue = []
        self.processing_lock = threading.Lock()
        
        # Worker thread
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_processing = threading.Event()
        
        # Verify spchcat installation
        self._verify_spchcat_installation()
        
        # Start processing worker
        self._start_worker()
    
    def _verify_spchcat_installation(self):
        """Verify that spchcat is properly installed and configured."""
        try:
            # Check if spchcat binary is available in PATH
            result = subprocess.run(
                ['which', 'spchcat'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                # Try the configured path
                if not os.path.exists(self.spchcat_path):
                    raise FileNotFoundError(f"spchcat binary not found. Install with: bash install/spchcat_setup.sh")
                else:
                    # Update path to the found binary
                    self.spchcat_path = result.stdout.strip()
            else:
                # Use the binary found in PATH
                self.spchcat_path = result.stdout.strip()
            
            # Check if binary is executable
            if not os.access(self.spchcat_path, os.X_OK):
                raise PermissionError(f"spchcat binary is not executable: {self.spchcat_path}")
            
            # Test spchcat with help command (version might not be available)
            result = subprocess.run(
                [self.spchcat_path, '--help'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"spchcat help check failed: {result.stderr}")
            
            self.logger.info(f"spchcat verification successful at: {self.spchcat_path}")
            
        except Exception as e:
            self.logger.error(f"spchcat installation verification failed: {e}")
            self.logger.error("Please run: bash install/spchcat_setup.sh to install spchcat")
            raise
    
    def _start_worker(self):
        """Start the speech processing worker thread."""
        if self.worker_thread and self.worker_thread.is_alive():
            self.logger.warning("Speech processing worker already running")
            return
        
        self.stop_processing.clear()
        self.worker_thread = threading.Thread(target=self._processing_worker, daemon=True)
        self.worker_thread.start()
        self.logger.info("Started speech processing worker")
    
    def set_processing_callback(self, callback: Callable):
        """
        Set callback function for processing completion.
        
        Args:
            callback: Function to call when STT processing is complete
                     Signature: callback(channel, audio_file, transcript, confidence, metadata)
        """
        self.processing_callback = callback
        self.logger.info("Speech processing callback registered")
    
    def process_audio_file(self, channel: int, audio_file_path: str, metadata: Dict) -> bool:
        """
        Queue audio file for speech-to-text processing.
        
        Args:
            channel: Audio channel (1-5)
            audio_file_path: Path to audio file
            metadata: Recording metadata
            
        Returns:
            True if file was queued successfully
        """
        if not os.path.exists(audio_file_path):
            self.logger.error(f"Audio file not found: {audio_file_path}")
            return False
        
        try:
            processing_item = {
                'channel': channel,
                'audio_file': audio_file_path,
                'metadata': metadata,
                'timestamp': datetime.now(),
                'status': 'queued'
            }
            
            with self.processing_lock:
                self.processing_queue.append(processing_item)
            
            self.logger.info(f"Queued audio file for processing: {audio_file_path} (channel {channel})")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to queue audio file for processing: {e}")
            return False
    
    def _processing_worker(self):
        """Worker thread for processing speech-to-text queue."""
        while not self.stop_processing.is_set():
            try:
                # Get next item from queue
                processing_item = None
                with self.processing_lock:
                    if self.processing_queue:
                        processing_item = self.processing_queue.pop(0)
                
                if processing_item:
                    self._process_speech_item(processing_item)
                else:
                    # No items to process, wait briefly
                    time.sleep(0.1)
                    
            except Exception as e:
                self.logger.error(f"Error in speech processing worker: {e}")
                time.sleep(1.0)
    
    def _process_speech_item(self, processing_item: Dict):
        """
        Process individual speech-to-text item using spchcat.
        
        Args:
            processing_item: Processing item dictionary
        """
        channel = processing_item['channel']
        audio_file = processing_item['audio_file']
        metadata = processing_item['metadata']
        
        # Check concurrent processing limit (Raspberry Pi optimization)
        with self.process_lock:
            if self.active_processes >= self.max_concurrent_processing:
                self.logger.debug(f"Waiting for available processing slot (current: {self.active_processes})")
                return  # Will be retried by worker
            
            self.active_processes += 1
        
        try:
            self.logger.info(f"Processing speech for channel {channel}: {audio_file}")
            
            # Validate audio file before processing
            if not self._validate_audio_file(audio_file):
                self.logger.warning(f"Audio file validation failed for channel {channel}: {audio_file}")
                return
            
            # Run spchcat on audio file
            transcript, confidence = self._run_spchcat(audio_file)
            
            if transcript:
                self.logger.info(f"Speech processing successful for channel {channel}: '{transcript}' (confidence: {confidence:.2f})")
                
                # Prepare result metadata
                result_metadata = {
                    **metadata,
                    'processing_time': datetime.now(),
                    'confidence': confidence,
                    'language': self.language,
                    'processor': 'spchcat'
                }
                
                # Save transcript to channel directory
                transcript_path = self._save_transcript(channel, audio_file, transcript, confidence, result_metadata)
                
                # Call completion callback (for legacy compatibility)
                if self.processing_callback:
                    try:
                        self.processing_callback(channel, audio_file, transcript, confidence, result_metadata)
                    except Exception as e:
                        self.logger.error(f"Error in processing callback: {e}")
                
                # Call transcript completion callback (for content filter integration)
                if self.transcript_complete_callback and transcript_path:
                    try:
                        self.transcript_complete_callback(channel, transcript_path, audio_file, result_metadata)
                    except Exception as e:
                        self.logger.error(f"Error in transcript completion callback: {e}")
            else:
                self.logger.warning(f"No transcript generated for channel {channel}: {audio_file}")
                
        except Exception as e:
            self.logger.error(f"Speech processing failed for channel {channel}: {e}")
        finally:
            # Always release the processing slot
            with self.process_lock:
                self.active_processes = max(0, self.active_processes - 1)
    
    def _run_spchcat(self, audio_file_path: str) -> tuple[Optional[str], float]:
        """
        Run spchcat on audio file to generate transcript.
        
        Args:
            audio_file_path: Path to audio file
            
        Returns:
            Tuple of (transcript, confidence) or (None, 0.0) if failed
        """
        try:
            # Prepare spchcat command (simplified for actual spchcat usage)
            cmd = [self.spchcat_path]
            
            # Add language if specified
            if self.language and self.language != 'en':
                cmd.extend(['--language', self.language])
            
            # Add audio file path
            cmd.append(audio_file_path)
            
            # Add additional spchcat options from config
            extra_options = self.spchcat_config.get('extra_options', [])
            if extra_options:
                cmd.extend(extra_options)
            
            self.logger.debug(f"Running spchcat command: {' '.join(cmd)}")
            
            # Execute spchcat with Raspberry Pi optimizations
            env = os.environ.copy()
            
            # Set process priority and memory limits for Raspberry Pi
            try:
                result = subprocess.run(
                    ['nice', '-n', str(self.process_priority)] + cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.processing_timeout,
                    cwd=self.temp_dir,
                    env=env
                )
            except FileNotFoundError:
                # Fallback if 'nice' is not available
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.processing_timeout,
                    cwd=self.temp_dir,
                    env=env
                )
            
            if result.returncode != 0:
                self.logger.error(f"spchcat failed with return code {result.returncode}: {result.stderr}")
                return None, 0.0
            
            # Parse spchcat output (plain text, not JSON)
            return self._parse_spchcat_output(result.stdout)
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"spchcat processing timed out after {self.processing_timeout} seconds")
            return None, 0.0
        except Exception as e:
            self.logger.error(f"spchcat execution failed: {e}")
            return None, 0.0
    
    def _parse_spchcat_output(self, output: str) -> tuple[Optional[str], float]:
        """
        Parse spchcat plain text output to extract transcript.
        
        Args:
            output: Raw spchcat output (plain text)
            
        Returns:
            Tuple of (transcript, confidence)
        """
        try:
            # spchcat outputs plain text, not JSON
            transcript = output.strip()
            
            if transcript:
                # spchcat doesn't provide confidence scores, so we estimate based on output quality
                confidence = self._estimate_confidence(transcript)
                return transcript, confidence
            else:
                return None, 0.0
                    
        except Exception as e:
            self.logger.error(f"Failed to parse spchcat output: {e}")
            return None, 0.0
    
    def _estimate_confidence(self, transcript: str) -> float:
        """
        Estimate confidence score based on transcript characteristics.
        
        Args:
            transcript: The transcript text
            
        Returns:
            Estimated confidence score between 0.0 and 1.0
        """
        try:
            # Basic heuristics for confidence estimation
            base_confidence = 0.7  # Default confidence for spchcat
            
            # Length bonus - longer transcripts tend to be more reliable
            length_bonus = min(0.1, len(transcript.split()) * 0.01)
            
            # Punctuation penalty - lots of punctuation might indicate unclear speech
            punctuation_chars = sum(1 for char in transcript if char in '.,!?;')
            punctuation_penalty = min(0.2, punctuation_chars * 0.05)
            
            # Word quality - penalize if many very short words (might be artifacts)
            words = transcript.split()
            short_words = sum(1 for word in words if len(word) <= 2)
            short_word_penalty = min(0.15, (short_words / len(words)) * 0.3) if words else 0
            
            # Calculate final confidence
            confidence = base_confidence + length_bonus - punctuation_penalty - short_word_penalty
            
            # Ensure within bounds
            return max(0.1, min(1.0, confidence))
            
        except Exception:
            # Return default confidence on any error
            return 0.7
    
    def get_queue_status(self) -> Dict:
        """
        Get current processing queue status.
        
        Returns:
            Dictionary with queue information
        """
        with self.processing_lock:
            return {
                'queue_length': len(self.processing_queue),
                'items': [
                    {
                        'channel': item['channel'],
                        'audio_file': os.path.basename(item['audio_file']),
                        'timestamp': item['timestamp'].isoformat(),
                        'status': item['status']
                    }
                    for item in self.processing_queue
                ]
            }
    
    def clear_queue(self):
        """Clear the processing queue."""
        with self.processing_lock:
            cleared_count = len(self.processing_queue)
            self.processing_queue.clear()
        
        self.logger.info(f"Cleared {cleared_count} items from processing queue")
    
    def test_spchcat(self) -> bool:
        """
        Test spchcat functionality with a simple audio file.
        
        Returns:
            True if spchcat test is successful
        """
        try:
            # Create a minimal test audio file or use existing one
            # For now, just test the command line interface
            result = subprocess.run(
                [self.spchcat_path, '--help'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            success = result.returncode == 0
            if success:
                self.logger.info("spchcat test successful")
            else:
                self.logger.error(f"spchcat test failed: {result.stderr}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"spchcat test failed: {e}")
            return False
    
    def stop_worker(self):
        """Stop the speech processing worker."""
        self.stop_processing.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=5.0)
        self.logger.info("Stopped speech processing worker")
    
    def cleanup(self):
        """Clean up speech processor resources."""
        try:
            self.stop_worker()
            self.clear_queue()
            self.logger.info("Speech processor cleanup completed")
        except Exception as e:
            self.logger.error(f"Speech processor cleanup failed: {e}")
    
    # =============================================================================
    # REQUIRED INTERFACES FOR SYSTEM INTEGRATION
    # =============================================================================
    
    def get_processed_transcripts(self, channel: Optional[int] = None) -> List[Dict]:
        """
        Get processed transcripts for Content Filter integration.
        
        Args:
            channel: Optional channel filter (1-5)
            
        Returns:
            List of transcript dictionaries with metadata
        """
        try:
            transcripts = []
            
            # Determine base directory for transcripts
            transcripts_dir = os.path.join(os.path.dirname(self.temp_dir), 'transcripts')
            
            if channel:
                # Search specific channel
                channel_dir = os.path.join(transcripts_dir, f"channel_{channel}")
                if os.path.exists(channel_dir):
                    for file_path in sorted(os.listdir(channel_dir)):
                        if file_path.endswith('.txt'):
                            full_path = os.path.join(channel_dir, file_path)
                            metadata = self.get_transcript_metadata(full_path)
                            if metadata:
                                transcripts.append(metadata)
            else:
                # Search all channels
                for ch in range(1, 6):
                    channel_dir = os.path.join(transcripts_dir, f"channel_{ch}")
                    if os.path.exists(channel_dir):
                        for file_path in sorted(os.listdir(channel_dir)):
                            if file_path.endswith('.txt'):
                                full_path = os.path.join(channel_dir, file_path)
                                metadata = self.get_transcript_metadata(full_path)
                                if metadata:
                                    transcripts.append(metadata)
            
            # Sort by processing time (newest first)
            transcripts.sort(key=lambda x: x.get('processing_time', ''), reverse=True)
            
            return transcripts
            
        except Exception as e:
            self.logger.error(f"Error getting processed transcripts: {e}")
            return []
    
    def get_transcript_metadata(self, file_path: str) -> Optional[Dict]:
        """
        Get metadata for a specific transcript file.
        
        Args:
            file_path: Path to transcript file
            
        Returns:
            Transcript metadata dictionary or None
        """
        try:
            if not os.path.exists(file_path):
                return None
            
            # Read transcript content
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # Basic file information
            stat = os.stat(file_path)
            
            # Extract channel from path
            channel = None
            for ch in range(1, 6):
                if f"channel_{ch}" in file_path:
                    channel = ch
                    break
            
            # Look for corresponding audio file
            audio_file = None
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            if channel:
                recordings_dir = os.path.join(os.path.dirname(self.temp_dir), 'recordings', f'channel_{channel}')
                for ext in ['.wav', '.mp3', '.flac']:
                    potential_audio = os.path.join(recordings_dir, base_name + ext)
                    if os.path.exists(potential_audio):
                        audio_file = potential_audio
                        break
            
            metadata = {
                'file_path': file_path,
                'filename': os.path.basename(file_path),
                'channel': channel,
                'transcript': content,
                'size_bytes': stat.st_size,
                'processing_time': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                'modified_time': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'audio_file': audio_file,
                'processor': 'spchcat'
            }
            
            return metadata
            
        except Exception as e:
            self.logger.error(f"Error getting metadata for {file_path}: {e}")
            return None
    
    def register_transcript_callback(self, callback: Callable):
        """
        Register callback function for transcript completion.
        
        Args:
            callback: Function to call when transcript processing is complete
                     Signature: callback(channel, transcript_file, audio_file, metadata)
        """
        # Store callback for use in processing completion
        self.transcript_complete_callback = callback
        self.logger.info("Transcript completion callback registered")
    
    def process_audio_file_by_path(self, file_path: str, channel: int) -> bool:
        """
        Process audio file for Queue Manager integration.
        
        Args:
            file_path: Path to audio file
            channel: Channel number (1-5)
            
        Returns:
            True if file was queued successfully
        """
        # Create metadata for the file
        metadata = {
            'file_path': file_path,
            'channel': channel,
            'queued_time': datetime.now().isoformat()
        }
        
        return self.process_audio_file(channel, file_path, metadata)
    
    def get_processing_status(self, file_path: str) -> Dict:
        """
        Get processing status for specific file.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Status dictionary
        """
        try:
            # Check if file is in processing queue
            with self.processing_lock:
                for item in self.processing_queue:
                    if item['audio_file'] == file_path:
                        return {
                            'file_path': file_path,
                            'status': 'queued',
                            'queued_time': item['timestamp'].isoformat(),
                            'channel': item['channel']
                        }
            
            # Check if transcript exists (processing completed)
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            
            # Look for transcript in all channel directories
            transcripts_dir = os.path.join(os.path.dirname(self.temp_dir), 'transcripts')
            for ch in range(1, 6):
                transcript_path = os.path.join(transcripts_dir, f"channel_{ch}", f"{base_name}.txt")
                if os.path.exists(transcript_path):
                    return {
                        'file_path': file_path,
                        'status': 'completed',
                        'transcript_path': transcript_path,
                        'channel': ch,
                        'completed_time': datetime.fromtimestamp(os.path.getctime(transcript_path)).isoformat()
                    }
            
            # File not found in queue or completed
            return {
                'file_path': file_path,
                'status': 'not_found'
            }
            
        except Exception as e:
            self.logger.error(f"Error getting processing status for {file_path}: {e}")
            return {
                'file_path': file_path,
                'status': 'error',
                'error': str(e)
            }
    
    def start_speech_processing(self) -> bool:
        """
        Start speech processing for Main Controller integration.
        
        Returns:
            True if started successfully
        """
        try:
            if not self.worker_thread or not self.worker_thread.is_alive():
                self._start_worker()
            
            self.logger.info("Speech processing started")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start speech processing: {e}")
            return False
    
    def stop_speech_processing(self) -> bool:
        """
        Stop speech processing for Main Controller integration.
        
        Returns:
            True if stopped successfully
        """
        try:
            self.stop_worker()
            self.logger.info("Speech processing stopped")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to stop speech processing: {e}")
            return False
    
    def get_processor_status(self) -> Dict:
        """
        Get processor status for Main Controller integration.
        
        Returns:
            Status dictionary
        """
        try:
            worker_alive = self.worker_thread and self.worker_thread.is_alive()
            
            with self.processing_lock:
                queue_length = len(self.processing_queue)
            
            status = {
                'timestamp': datetime.now().isoformat(),
                'worker_active': worker_alive,
                'queue_length': queue_length,
                'spchcat_path': self.spchcat_path,
                'language': self.language,
                'processing_timeout': self.processing_timeout,
                'confidence_threshold': self.confidence_threshold,
                'temp_dir': self.temp_dir
            }
            
            return status
            
        except Exception as e:
            self.logger.error(f"Error getting processor status: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'status': 'error'
            }
    
    def emergency_stop(self) -> bool:
        """
        Emergency stop all speech processing.
        
        Returns:
            True if emergency stop successful
        """
        try:
            self.logger.warning("Emergency stop activated for speech processing")
            
            # Stop worker immediately
            self.stop_processing.set()
            
            # Clear queue
            self.clear_queue()
            
            # Stop worker thread
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=2.0)
                if self.worker_thread.is_alive():
                    self.logger.warning("Worker thread did not stop gracefully during emergency stop")
            
            self.logger.warning("Speech processing emergency stop completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Emergency stop failed: {e}")
            return False
    
    def _save_transcript(self, channel: int, audio_file: str, transcript: str, confidence: float, metadata: Dict) -> Optional[str]:
        """
        Save transcript to channel-specific directory.
        
        Args:
            channel: Channel number (1-5)
            audio_file: Original audio file path
            transcript: Transcript text
            confidence: Confidence score
            metadata: Processing metadata
            
        Returns:
            Path to saved transcript file or None if failed
        """
        try:
            # Create transcripts directory structure
            transcripts_dir = os.path.join(os.path.dirname(self.temp_dir), 'transcripts')
            channel_dir = os.path.join(transcripts_dir, f"channel_{channel}")
            os.makedirs(channel_dir, exist_ok=True)
            
            # Generate transcript filename based on audio file
            audio_basename = os.path.splitext(os.path.basename(audio_file))[0]
            transcript_filename = f"{audio_basename}.txt"
            transcript_path = os.path.join(channel_dir, transcript_filename)
            
            # Ensure unique filename
            counter = 1
            while os.path.exists(transcript_path):
                transcript_filename = f"{audio_basename}_{counter}.txt"
                transcript_path = os.path.join(channel_dir, transcript_filename)
                counter += 1
            
            # Write transcript file
            with open(transcript_path, 'w', encoding='utf-8') as f:
                f.write(transcript)
            
            # Write metadata file
            metadata_path = transcript_path.replace('.txt', '_metadata.json')
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump({
                    **metadata,
                    'transcript_path': transcript_path,
                    'confidence': confidence,
                    'audio_file': audio_file,
                    'channel': channel
                }, f, indent=2)
            
            self.logger.info(f"Transcript saved to: {transcript_path}")
            return transcript_path
            
        except Exception as e:
            self.logger.error(f"Failed to save transcript for channel {channel}: {e}")
            return None
    
    def _validate_audio_file(self, audio_file_path: str) -> bool:
        """
        Validate audio file before processing.
        
        Args:
            audio_file_path: Path to audio file
            
        Returns:
            True if audio file is valid for processing
        """
        try:
            # Check if file exists
            if not os.path.exists(audio_file_path):
                self.logger.error(f"Audio file does not exist: {audio_file_path}")
                return False
            
            # Check file size
            file_size = os.path.getsize(audio_file_path)
            if file_size == 0:
                self.logger.error(f"Audio file is empty: {audio_file_path}")
                return False
            
            # Check audio duration if sample rate check is enabled
            if self.sample_rate_check:
                try:
                    with wave.open(audio_file_path, 'rb') as wav_file:
                        frames = wav_file.getnframes()
                        sample_rate = wav_file.getframerate()
                        duration = frames / sample_rate
                        
                        # Check duration limits
                        if duration < self.min_audio_duration:
                            self.logger.warning(f"Audio file too short ({duration:.2f}s < {self.min_audio_duration}s): {audio_file_path}")
                            return False
                        
                        if duration > self.max_audio_duration:
                            self.logger.warning(f"Audio file too long ({duration:.2f}s > {self.max_audio_duration}s): {audio_file_path}")
                            return False
                        
                        self.logger.debug(f"Audio validation passed: {duration:.2f}s, {sample_rate}Hz")
                        
                except Exception as e:
                    self.logger.warning(f"Could not validate audio file format: {e}")
                    # Continue anyway - spchcat might handle it
            
            return True
            
        except Exception as e:
            self.logger.error(f"Audio file validation error: {e}")
            return False