#!/usr/bin/env python3
"""
Speech Processing Integration Example for Raspberry Pi Audio System.

This example demonstrates how the SpeechProcessor integrates with the
AudioRecorder to provide complete speech-to-text functionality using spchcat.

MANDATORY: This system uses spchcat exclusively for speech-to-text processing.
"""

import logging
import time
import os
from typing import Dict

from speech_processor import SpeechProcessor
from recorder import AudioRecorder


def setup_logging():
    """Setup logging for the integration example."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def create_sample_config() -> Dict:
    """Create sample configuration for testing."""
    return {
        'spchcat': {
            'binary_path': '/usr/local/bin/spchcat',
            'language': 'en',
            'timeout': 30,
            'confidence_threshold': 0.7,
            'max_concurrent_processing': 1,
            'process_priority': 10,
            'memory_limit_mb': 512,
            'min_audio_duration': 1.0,
            'max_audio_duration': 60.0,
            'sample_rate_check': True,
            'extra_options': []
        },
        'paths': {
            'temp': './temp',
            'recordings': './recordings',
            'transcripts': './transcripts'
        }
    }


def transcript_completion_callback(channel: int, transcript_file: str, audio_file: str, metadata: Dict):
    """
    Callback function for transcript completion.
    
    This would typically be used by the Content Filter to process completed transcripts.
    """
    print(f"\n=== TRANSCRIPT COMPLETED ===")
    print(f"Channel: {channel}")
    print(f"Audio File: {audio_file}")
    print(f"Transcript File: {transcript_file}")
    print(f"Confidence: {metadata.get('confidence', 'N/A')}")
    
    # Read transcript content
    try:
        with open(transcript_file, 'r', encoding='utf-8') as f:
            transcript = f.read().strip()
        print(f"Transcript: '{transcript}'")
    except Exception as e:
        print(f"Error reading transcript: {e}")
    
    print("=" * 30)


def recording_completion_callback(channel: int, file_path: str, metadata: Dict):
    """
    Callback function for recording completion.
    
    This demonstrates how the AudioRecorder integrates with SpeechProcessor.
    """
    print(f"\n--- Recording completed on channel {channel}: {file_path}")
    
    # The speech processor would automatically pick up this file
    # through the completion callback integration
    

def main():
    """Main integration example."""
    setup_logging()
    logger = logging.getLogger(__name__)
    
    print("Speech Processing Integration Example")
    print("=" * 50)
    print("This example demonstrates spchcat integration with the audio recording system.")
    print("MANDATORY: spchcat must be installed via: bash install/spchcat_setup.sh")
    print()
    
    # Create configuration
    config = create_sample_config()
    
    try:
        # Initialize speech processor
        print("Initializing SpeechProcessor...")
        speech_processor = SpeechProcessor(config)
        
        # Register transcript completion callback
        speech_processor.register_transcript_callback(transcript_completion_callback)
        
        print("✓ SpeechProcessor initialized successfully")
        print(f"✓ spchcat path: {speech_processor.spchcat_path}")
        print(f"✓ Language: {speech_processor.language}")
        print(f"✓ Confidence threshold: {speech_processor.confidence_threshold}")
        print()
        
        # Test spchcat functionality
        print("Testing spchcat installation...")
        if speech_processor.test_spchcat():
            print("✓ spchcat test passed")
        else:
            print("✗ spchcat test failed")
            print("Please install spchcat: bash install/spchcat_setup.sh")
            return
        
        print()
        
        # Get processor status
        status = speech_processor.get_processor_status()
        print("Speech Processor Status:")
        for key, value in status.items():
            print(f"  {key}: {value}")
        
        print()
        
        # Demonstrate interface methods
        print("Testing interface methods...")
        
        # Test get_processed_transcripts (should be empty initially)
        transcripts = speech_processor.get_processed_transcripts()
        print(f"✓ get_processed_transcripts: {len(transcripts)} transcripts found")
        
        # Test queue status
        queue_status = speech_processor.get_queue_status()
        print(f"✓ Queue status: {queue_status['queue_length']} items in queue")
        
        print()
        print("Integration example completed successfully!")
        print()
        print("INTEGRATION NOTES:")
        print("- The SpeechProcessor automatically processes audio files from AudioRecorder")
        print("- Transcripts are saved to transcripts/channel_X/ directories")
        print("- Content Filter receives transcript completion callbacks")
        print("- Queue Manager can check processing status via get_processing_status()")
        print("- Main Controller uses start/stop methods for system control")
        print()
        print("NEXT STEPS:")
        print("1. Agent 5 (Content Filter) will use get_processed_transcripts() and register_transcript_callback()")
        print("2. Agent 6 (Queue Manager) will use process_audio_file_by_path() and get_processing_status()")
        print("3. Agent 9 (Main Controller) will use start/stop_speech_processing() and emergency_stop()")
        
    except Exception as e:
        logger.error(f"Integration example failed: {e}")
        print(f"\n✗ Error: {e}")
        print("\nTroubleshooting:")
        print("1. Ensure spchcat is installed: bash install/spchcat_setup.sh")
        print("2. Check that /usr/local/bin is in your PATH")
        print("3. Verify PulseAudio is installed and running")
        
    finally:
        # Cleanup
        try:
            speech_processor.cleanup()
            print("\n✓ Cleanup completed")
        except:
            pass


if __name__ == "__main__":
    main()