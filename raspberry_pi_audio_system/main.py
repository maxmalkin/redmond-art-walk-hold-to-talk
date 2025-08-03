#!/usr/bin/env python3
"""
Raspberry Pi Multi-Button Audio Recording & Processing System
Main Controller

This is the central controller that orchestrates all system components:
- 5 GPIO recording buttons (hold-to-record)
- 5 GPIO playback buttons (momentary press) 
- USB microphone input
- 5 USB audio outputs
- spchcat speech-to-text processing (MANDATORY)
- Content filtering and file management
- Channel mapping preservation throughout pipeline

Author: System Architect
Version: 1.0.0
"""

import sys
import os
import signal
import logging
import threading
import time
from typing import Optional
from datetime import datetime

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import system modules
from utils.config import ConfigManager, ConfigurationError
from utils.file_manager import FileManager
from hardware.gpio_handler import GPIOHandler
from hardware.audio_devices import AudioDeviceManager
from processing.recorder import AudioRecorder
from processing.speech_processor import SpeechProcessor
from processing.content_filter import ContentFilter
from task_queue.file_queue import FileProcessingQueue
from playback.output_manager import AudioOutputManager


class RaspberryPiAudioSystem:
    """
    Main system controller for the Raspberry Pi Audio Recording & Processing System.
    
    Coordinates all system components and maintains channel mapping throughout
    the audio processing pipeline.
    """
    
    def __init__(self, config_file: str = "config.yaml"):
        """
        Initialize the audio system.
        
        Args:
            config_file: Path to configuration file
        """
        self.config_file = config_file
        self.logger = self._setup_logging()
        
        # System components
        self.config_manager: Optional[ConfigManager] = None
        self.file_manager: Optional[FileManager] = None
        self.gpio_handler: Optional[GPIOHandler] = None
        self.audio_device_manager: Optional[AudioDeviceManager] = None
        self.audio_recorder: Optional[AudioRecorder] = None
        self.speech_processor: Optional[SpeechProcessor] = None
        self.content_filter: Optional[ContentFilter] = None
        self.processing_queue: Optional[FileProcessingQueue] = None
        self.output_manager: Optional[AudioOutputManager] = None
        
        # System state
        self.system_running = False
        self.initialization_complete = False
        
        # Monitoring thread
        self.monitor_thread: Optional[threading.Thread] = None
        self.stop_monitoring = threading.Event()
        
        # Setup signal handlers
        self._setup_signal_handlers()
    
    def _setup_logging(self) -> logging.Logger:
        """Setup basic logging before configuration is loaded."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(__name__)
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"Received signal {signum}, initiating shutdown...")
        self.stop()
    
    def initialize(self) -> bool:
        """
        Initialize all system components.
        
        Returns:
            True if initialization successful
        """
        try:
            self.logger.info("Initializing Raspberry Pi Audio System...")
            
            # Load configuration
            self.logger.info("Loading configuration...")
            self.config_manager = ConfigManager(self.config_file)
            config = self.config_manager.config
            
            # Setup proper logging with configuration
            self._setup_configured_logging(config)
            
            # Validate runtime requirements
            runtime_issues = self.config_manager.validate_runtime_requirements()
            if runtime_issues:
                for issue in runtime_issues:
                    self.logger.error(f"Runtime requirement issue: {issue}")
                return False
            
            # Initialize file manager
            self.logger.info("Initializing file manager...")
            self.file_manager = FileManager(config)
            
            # Initialize audio device manager
            self.logger.info("Initializing audio device manager...")
            self.audio_device_manager = AudioDeviceManager(config)
            
            # Test audio device connectivity
            device_tests = self.audio_device_manager.test_device_connectivity()
            if not device_tests.get('input', False):
                self.logger.error("Input device (microphone) test failed")
                return False
            
            output_channels_ok = sum(1 for i in range(1, 6) if device_tests.get(f'output_channel_{i}', False))
            if output_channels_ok < 5:
                self.logger.warning(f"Only {output_channels_ok}/5 output channels passed connectivity test")
            
            # Initialize speech processor (spchcat - MANDATORY)
            self.logger.info("Initializing speech processor (spchcat)...")
            self.speech_processor = SpeechProcessor(config)
            
            # Test spchcat functionality
            if not self.speech_processor.test_spchcat():
                self.logger.error("spchcat test failed - speech processing will not work")
                return False
            
            # Initialize content filter
            self.logger.info("Initializing content filter...")
            self.content_filter = ContentFilter(config)
            
            # Initialize processing queue
            self.logger.info("Initializing processing queue...")
            self.processing_queue = FileProcessingQueue(
                self.speech_processor, 
                self.content_filter, 
                config
            )
            
            # Initialize audio recorder
            self.logger.info("Initializing audio recorder...")
            self.audio_recorder = AudioRecorder(self.audio_device_manager, config)
            
            # Initialize output manager
            self.logger.info("Initializing audio output manager...")
            self.output_manager = AudioOutputManager(
                self.audio_device_manager,
                self.content_filter,
                config
            )
            
            # Initialize GPIO handler
            self.logger.info("Initializing GPIO handler...")
            self.gpio_handler = GPIOHandler(config)
            
            # Setup component callbacks and connections
            self._setup_component_callbacks()
            
            # Start monitoring threads
            self._start_monitoring()
            
            self.initialization_complete = True
            self.logger.info("System initialization completed successfully")
            
            # Log system summary
            self._log_system_summary()
            
            return True
            
        except Exception as e:
            self.logger.error(f"System initialization failed: {e}")
            return False
    
    def _setup_configured_logging(self, config):
        """Setup logging with configuration settings."""
        try:
            log_config = config.get('logging', {})
            log_level = getattr(logging, log_config.get('level', 'INFO'))
            log_format = log_config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            log_file = log_config.get('file', './logs/system.log')
            
            # Ensure log directory exists
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            # Configure root logger
            logging.basicConfig(
                level=log_level,
                format=log_format,
                handlers=[
                    logging.FileHandler(log_file),
                    logging.StreamHandler(sys.stdout)
                ],
                force=True
            )
            
            self.logger = logging.getLogger(__name__)
            self.logger.info("Logging configuration applied")
            
        except Exception as e:
            self.logger.error(f"Failed to setup configured logging: {e}")
    
    def _setup_component_callbacks(self):
        """Setup callbacks between system components."""
        try:
            # Recording completion callback
            self.audio_recorder.set_recording_complete_callback(
                self._on_recording_complete
            )
            
            # GPIO button callbacks
            for channel in range(1, 6):
                self.gpio_handler.register_recording_callback(
                    channel, self._on_recording_button_event
                )
                self.gpio_handler.register_playback_callback(
                    channel, self._on_playback_button_event
                )
            
            # Processing queue completion callback
            self.processing_queue.add_completion_callback(
                self._on_processing_complete
            )
            
            # Output manager callbacks
            self.output_manager.add_playback_callback(
                self._on_playback_event
            )
            
            self.logger.info("Component callbacks configured")
            
        except Exception as e:
            self.logger.error(f"Failed to setup component callbacks: {e}")
            raise
    
    def _on_recording_button_event(self, channel: int, action: str):
        """Handle recording button events."""
        try:
            if action == "start_recording":
                success = self.audio_recorder.start_recording(channel)
                if success:
                    self.logger.info(f"Started recording on channel {channel}")
                else:
                    self.logger.error(f"Failed to start recording on channel {channel}")
            
            elif action == "stop_recording":
                file_path = self.audio_recorder.stop_recording(channel)
                if file_path:
                    self.logger.info(f"Stopped recording on channel {channel}: {file_path}")
                else:
                    self.logger.error(f"Failed to stop recording on channel {channel}")
            
        except Exception as e:
            self.logger.error(f"Error handling recording button event: {e}")
    
    def _on_playback_button_event(self, channel: int):
        """Handle playback button events."""
        try:
            success = self.output_manager.trigger_playback(channel)
            if success:
                self.logger.info(f"Triggered playback on channel {channel}")
            else:
                self.logger.warning(f"Playback trigger failed on channel {channel}")
                
        except Exception as e:
            self.logger.error(f"Error handling playback button event: {e}")
    
    def _on_recording_complete(self, channel: int, file_path: str, metadata: dict):
        """Handle recording completion."""
        try:
            self.logger.info(f"Recording completed on channel {channel}: {file_path}")
            
            # Submit to processing queue
            task_id = self.processing_queue.submit_task(channel, file_path, metadata)
            self.logger.info(f"Submitted processing task {task_id} for channel {channel}")
            
        except Exception as e:
            self.logger.error(f"Error handling recording completion: {e}")
    
    def _on_processing_complete(self, task):
        """Handle processing task completion."""
        try:
            if task.status.value == 'completed':
                self.logger.info(f"Processing completed for task {task.task_id} (channel {task.channel})")
                
                # Get filter results
                if task.result and 'filter_result' in task.result:
                    filter_result = task.result['filter_result']
                    is_clean = filter_result.get('is_clean', False)
                    destination = filter_result.get('destination_file', 'unknown')
                    
                    status = "CLEAN" if is_clean else "FILTERED"
                    self.logger.info(f"Channel {task.channel} audio processed as {status}: {destination}")
            else:
                self.logger.error(f"Processing failed for task {task.task_id}: {task.error_message}")
                
        except Exception as e:
            self.logger.error(f"Error handling processing completion: {e}")
    
    def _on_playback_event(self, event: str, channel: int, file_path: Optional[str], details: dict):
        """Handle playback events."""
        try:
            if event == "started":
                self.logger.info(f"Playback started on channel {channel}: {os.path.basename(file_path) if file_path else 'unknown'}")
            elif event == "completed":
                duration = details.get('duration', 0)
                self.logger.info(f"Playback completed on channel {channel} (duration: {duration:.1f}s)")
            elif event == "failed":
                error = details.get('error', 'unknown')
                self.logger.error(f"Playback failed on channel {channel}: {error}")
            elif event == "no_file":
                self.logger.warning(f"No playable files available for channel {channel}")
                
        except Exception as e:
            self.logger.error(f"Error handling playback event: {e}")
    
    def _start_monitoring(self):
        """Start system monitoring threads."""
        try:
            # Start GPIO monitoring
            self.gpio_handler.start_monitoring()
            
            # Start system monitoring thread
            self.monitor_thread = threading.Thread(
                target=self._monitoring_worker,
                daemon=True,
                name="SystemMonitor"
            )
            self.monitor_thread.start()
            
            self.logger.info("System monitoring started")
            
        except Exception as e:
            self.logger.error(f"Failed to start monitoring: {e}")
            raise
    
    def _monitoring_worker(self):
        """System monitoring worker thread."""
        heartbeat_interval = self.config_manager.get('system.heartbeat_interval', 30)
        cleanup_interval = self.config_manager.get('system.auto_cleanup_interval', 3600)
        
        last_cleanup = time.time()
        
        while not self.stop_monitoring.is_set():
            try:
                # System heartbeat
                if self.system_running:
                    self._log_system_status()
                
                # Periodic cleanup
                current_time = time.time()
                if current_time - last_cleanup > cleanup_interval:
                    self._perform_maintenance()
                    last_cleanup = current_time
                
                # Wait for next cycle
                self.stop_monitoring.wait(heartbeat_interval)
                
            except Exception as e:
                self.logger.error(f"Monitoring worker error: {e}")
                time.sleep(10)  # Wait before retrying
    
    def _log_system_status(self):
        """Log current system status."""
        try:
            # Get active recordings
            active_recordings = self.audio_recorder.get_active_recordings()
            
            # Get queue status
            queue_status = self.processing_queue.get_queue_status()
            
            # Get playback status
            playback_status = self.output_manager.get_active_playback_info()
            
            # Log summary
            self.logger.debug(f"System Status - Active recordings: {len(active_recordings)}, "
                             f"Queue: {queue_status['queue_size']}, "
                             f"Active playback: {len(playback_status)}")
            
        except Exception as e:
            self.logger.error(f"Error logging system status: {e}")
    
    def _perform_maintenance(self):
        """Perform routine system maintenance."""
        try:
            self.logger.info("Performing system maintenance...")
            
            # File maintenance
            if self.file_manager:
                results = self.file_manager.perform_maintenance()
                self.logger.info(f"Maintenance results: {results}")
            
            # Clear old processing tasks
            if self.processing_queue:
                self.processing_queue.clear_completed_tasks()
            
            self.logger.info("System maintenance completed")
            
        except Exception as e:
            self.logger.error(f"System maintenance failed: {e}")
    
    def _log_system_summary(self):
        """Log system initialization summary."""
        try:
            # Configuration summary
            config_summary = self.config_manager.get_config_summary()
            
            # Device information
            device_info = self.audio_device_manager.get_device_info()
            
            # Channel mapping
            channel_mapping = self.gpio_handler.get_channel_mapping()
            
            self.logger.info("=== SYSTEM INITIALIZATION SUMMARY ===")
            self.logger.info(f"Configuration: {config_summary}")
            self.logger.info(f"Audio devices: Input={device_info.get('input_device', {}).get('name', 'None')}, "
                           f"Outputs={len(device_info.get('output_devices', {}))}")
            self.logger.info(f"GPIO channels configured: {len(channel_mapping)}")
            self.logger.info(f"spchcat version: {self.speech_processor.spchcat_config}")
            self.logger.info("======================================")
            
        except Exception as e:
            self.logger.error(f"Error logging system summary: {e}")
    
    def start(self) -> bool:
        """
        Start the audio system.
        
        Returns:
            True if system started successfully
        """
        try:
            if not self.initialization_complete:
                self.logger.error("System not initialized - call initialize() first")
                return False
            
            if self.system_running:
                self.logger.warning("System is already running")
                return True
            
            self.logger.info("Starting Raspberry Pi Audio System...")
            
            # Apply startup delay
            startup_delay = self.config_manager.get('system.startup_delay', 2)
            if startup_delay > 0:
                self.logger.info(f"Applying startup delay: {startup_delay} seconds")
                time.sleep(startup_delay)
            
            self.system_running = True
            
            self.logger.info("Raspberry Pi Audio System is now running")
            self.logger.info("System ready - waiting for button events...")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start system: {e}")
            return False
    
    def stop(self):
        """Stop the audio system gracefully."""
        try:
            self.logger.info("Stopping Raspberry Pi Audio System...")
            
            self.system_running = False
            
            # Stop monitoring
            self.stop_monitoring.set()
            if self.monitor_thread:
                self.monitor_thread.join(timeout=5.0)
            
            # Stop system components
            if self.gpio_handler:
                self.gpio_handler.stop_monitoring_buttons()
            
            if self.audio_recorder:
                self.audio_recorder.stop_all_recordings()
            
            if self.output_manager:
                self.output_manager.stop_all_playback()
            
            if self.processing_queue:
                self.processing_queue.stop_workers()
            
            if self.speech_processor:
                self.speech_processor.stop_worker()
            
            # Cleanup components
            components = [
                self.audio_recorder,
                self.speech_processor,
                self.content_filter,
                self.processing_queue,
                self.output_manager,
                self.gpio_handler,
                self.audio_device_manager,
                self.file_manager
            ]
            
            for component in components:
                if component and hasattr(component, 'cleanup'):
                    try:
                        component.cleanup()
                    except Exception as e:
                        self.logger.error(f"Error cleaning up component {type(component).__name__}: {e}")
            
            self.logger.info("Raspberry Pi Audio System stopped")
            
        except Exception as e:
            self.logger.error(f"Error during system shutdown: {e}")
    
    def run(self):
        """Run the system (blocking until stopped)."""
        try:
            if not self.start():
                return False
            
            # Main event loop
            while self.system_running:
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    self.logger.info("Keyboard interrupt received")
                    break
            
            return True
            
        except Exception as e:
            self.logger.error(f"Runtime error: {e}")
            return False
        finally:
            self.stop()


def main():
    """Main entry point."""
    try:
        # Parse command line arguments
        import argparse
        parser = argparse.ArgumentParser(description='Raspberry Pi Audio Recording & Processing System')
        parser.add_argument('--config', '-c', default='config.yaml', 
                          help='Configuration file path (default: config.yaml)')
        parser.add_argument('--test-mode', action='store_true',
                          help='Run in test mode (initialize only, no GPIO)')
        parser.add_argument('--verbose', '-v', action='store_true',
                          help='Enable verbose logging')
        
        args = parser.parse_args()
        
        # Set initial log level
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        # Create and initialize system
        system = RaspberryPiAudioSystem(args.config)
        
        if not system.initialize():
            print("ERROR: System initialization failed")
            return 1
        
        if args.test_mode:
            print("Test mode: System initialized successfully")
            system.stop()
            return 0
        
        # Run system
        print("Starting Raspberry Pi Audio System...")
        print("Press Ctrl+C to stop")
        
        success = system.run()
        return 0 if success else 1
        
    except ConfigurationError as e:
        print(f"Configuration Error: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())