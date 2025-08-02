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
        
        # File paths
        self.temp_dir = config.get('paths', {}).get('temp', './temp')
        
        # Processing queue and callbacks
        self.processing_callback: Optional[Callable] = None
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
            # Check if spchcat binary exists
            if not os.path.exists(self.spchcat_path):
                raise FileNotFoundError(f"spchcat binary not found at: {self.spchcat_path}")
            
            # Check if binary is executable
            if not os.access(self.spchcat_path, os.X_OK):
                raise PermissionError(f"spchcat binary is not executable: {self.spchcat_path}")
            
            # Check if model directory exists
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"spchcat model directory not found: {self.model_path}")
            
            # Test spchcat with version command
            result = subprocess.run(
                [self.spchcat_path, '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"spchcat version check failed: {result.stderr}")
            
            self.logger.info(f"spchcat verification successful: {result.stdout.strip()}")
            
        except Exception as e:
            self.logger.error(f"spchcat installation verification failed: {e}")
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
        
        try:
            self.logger.info(f"Processing speech for channel {channel}: {audio_file}")
            
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
                
                # Call completion callback
                if self.processing_callback:
                    try:
                        self.processing_callback(channel, audio_file, transcript, confidence, result_metadata)
                    except Exception as e:
                        self.logger.error(f"Error in processing callback: {e}")
            else:
                self.logger.warning(f"No transcript generated for channel {channel}: {audio_file}")
                
        except Exception as e:
            self.logger.error(f"Speech processing failed for channel {channel}: {e}")
    
    def _run_spchcat(self, audio_file_path: str) -> tuple[Optional[str], float]:
        """
        Run spchcat on audio file to generate transcript.
        
        Args:
            audio_file_path: Path to audio file
            
        Returns:
            Tuple of (transcript, confidence) or (None, 0.0) if failed
        """
        try:
            # Prepare spchcat command
            cmd = [
                self.spchcat_path,
                '--input', audio_file_path,
                '--language', self.language,
                '--model-path', self.model_path,
                '--output-format', 'json',
                '--confidence-threshold', str(self.confidence_threshold)
            ]
            
            # Add additional spchcat options from config
            extra_options = self.spchcat_config.get('extra_options', [])
            if extra_options:
                cmd.extend(extra_options)
            
            self.logger.debug(f"Running spchcat command: {' '.join(cmd)}")
            
            # Execute spchcat
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.processing_timeout,
                cwd=self.temp_dir
            )
            
            if result.returncode != 0:
                self.logger.error(f"spchcat failed with return code {result.returncode}: {result.stderr}")
                return None, 0.0
            
            # Parse spchcat output
            return self._parse_spchcat_output(result.stdout)
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"spchcat processing timed out after {self.processing_timeout} seconds")
            return None, 0.0
        except Exception as e:
            self.logger.error(f"spchcat execution failed: {e}")
            return None, 0.0
    
    def _parse_spchcat_output(self, output: str) -> tuple[Optional[str], float]:
        """
        Parse spchcat JSON output to extract transcript and confidence.
        
        Args:
            output: Raw spchcat output
            
        Returns:
            Tuple of (transcript, confidence)
        """
        try:
            # Try to parse as JSON first
            try:
                data = json.loads(output)
                transcript = data.get('transcript', '').strip()
                confidence = float(data.get('confidence', 0.0))
                return transcript if transcript else None, confidence
            except json.JSONDecodeError:
                # Fallback: treat as plain text transcript
                transcript = output.strip()
                if transcript:
                    # Assume reasonable confidence for plain text output
                    return transcript, 0.8
                else:
                    return None, 0.0
                    
        except Exception as e:
            self.logger.error(f"Failed to parse spchcat output: {e}")
            return None, 0.0
    
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