"""
Content Filter for Raspberry Pi Audio System.

Filters processed speech transcripts for inappropriate content and
determines file placement (bin vs playable folder).
"""

import logging
import re
from typing import List, Dict, Optional, Set
import os
import shutil


class ContentFilter:
    """
    Content filter for speech transcript processing.
    
    Evaluates transcripts against filtered word lists and determines
    appropriate file placement and handling.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize content filter with configuration.
        
        Args:
            config: Configuration dictionary containing filter settings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Filter configuration
        self.filter_config = config.get('content_filter', {})
        self.strict_mode = self.filter_config.get('strict_mode', True)
        self.case_sensitive = self.filter_config.get('case_sensitive', False)
        
        # Load filtered words
        self.filtered_words: Set[str] = set()
        self.filtered_phrases: Set[str] = set()
        self._load_filtered_content()
        
        # File paths
        self.bin_dir = config.get('paths', {}).get('bin', './bin')
        self.playable_dir = config.get('paths', {}).get('playable', './playable')
        
        # Ensure directories exist
        self._create_directories()
        
        # Processing callback
        self.filter_callback: Optional[callable] = None
    
    def _load_filtered_content(self):
        """Load filtered words and phrases from configuration."""
        try:
            # Load filtered words
            filtered_words = self.filter_config.get('filtered_words', [])
            for word in filtered_words:
                if not self.case_sensitive:
                    word = word.lower()
                self.filtered_words.add(word.strip())
            
            # Load filtered phrases
            filtered_phrases = self.filter_config.get('filtered_phrases', [])
            for phrase in filtered_phrases:
                if not self.case_sensitive:
                    phrase = phrase.lower()
                self.filtered_phrases.add(phrase.strip())
            
            self.logger.info(f"Loaded {len(self.filtered_words)} filtered words and {len(self.filtered_phrases)} filtered phrases")
            
        except Exception as e:
            self.logger.error(f"Failed to load filtered content: {e}")
    
    def _create_directories(self):
        """Create necessary directories for file management."""
        try:
            os.makedirs(self.bin_dir, exist_ok=True)
            os.makedirs(self.playable_dir, exist_ok=True)
            
            # Create channel subdirectories
            for channel in range(1, 6):
                channel_bin_dir = os.path.join(self.bin_dir, f"channel_{channel}")
                channel_playable_dir = os.path.join(self.playable_dir, f"channel_{channel}")
                os.makedirs(channel_bin_dir, exist_ok=True)
                os.makedirs(channel_playable_dir, exist_ok=True)
            
            self.logger.info(f"Created filter directories: {self.bin_dir}, {self.playable_dir}")
            
        except Exception as e:
            self.logger.error(f"Failed to create filter directories: {e}")
            raise
    
    def set_filter_callback(self, callback: callable):
        """
        Set callback function for filter processing completion.
        
        Args:
            callback: Function to call when filtering is complete
                     Signature: callback(channel, file_path, is_clean, filter_results)
        """
        self.filter_callback = callback
        self.logger.info("Content filter callback registered")
    
    def process_transcript(self, channel: int, audio_file_path: str, transcript: str, 
                          confidence: float, metadata: Dict) -> Dict:
        """
        Process transcript through content filter and move file accordingly.
        
        Args:
            channel: Audio channel (1-5)
            audio_file_path: Path to original audio file
            transcript: Speech transcript text
            confidence: Transcript confidence score
            metadata: Processing metadata
            
        Returns:
            Filter results dictionary
        """
        try:
            self.logger.info(f"Processing transcript for channel {channel}: '{transcript}'")
            
            # Perform content analysis
            filter_results = self._analyze_content(transcript)
            
            # Determine if content is clean
            is_clean = filter_results['is_clean']
            
            # Move file to appropriate directory
            destination_path = self._move_file(channel, audio_file_path, is_clean)
            
            # Update filter results
            filter_results.update({
                'channel': channel,
                'original_file': audio_file_path,
                'destination_file': destination_path,
                'transcript': transcript,
                'confidence': confidence,
                'metadata': metadata,
                'processing_time': metadata.get('processing_time')
            })
            
            # Log results
            status = "CLEAN" if is_clean else "FILTERED"
            self.logger.info(f"Channel {channel} content {status}: moved to {destination_path}")
            
            # Call completion callback
            if self.filter_callback:
                try:
                    self.filter_callback(channel, destination_path, is_clean, filter_results)
                except Exception as e:
                    self.logger.error(f"Error in filter callback: {e}")
            
            return filter_results
            
        except Exception as e:
            self.logger.error(f"Content filter processing failed for channel {channel}: {e}")
            return {
                'channel': channel,
                'is_clean': False,
                'error': str(e),
                'filtered_words_found': [],
                'filtered_phrases_found': []
            }
    
    def _analyze_content(self, transcript: str) -> Dict:
        """
        Analyze transcript content for filtered words and phrases.
        
        Args:
            transcript: Speech transcript to analyze
            
        Returns:
            Analysis results dictionary
        """
        if not transcript:
            return {
                'is_clean': True,
                'filtered_words_found': [],
                'filtered_phrases_found': [],
                'analysis_confidence': 'high'
            }
        
        # Prepare text for analysis
        analysis_text = transcript
        if not self.case_sensitive:
            analysis_text = transcript.lower()
        
        # Check for filtered words
        filtered_words_found = []
        for word in self.filtered_words:
            # Use word boundary matching for more accurate detection
            pattern = r'\b' + re.escape(word) + r'\b'
            if re.search(pattern, analysis_text):
                filtered_words_found.append(word)
        
        # Check for filtered phrases
        filtered_phrases_found = []
        for phrase in self.filtered_phrases:
            if phrase in analysis_text:
                filtered_phrases_found.append(phrase)
        
        # Determine if content is clean
        is_clean = len(filtered_words_found) == 0 and len(filtered_phrases_found) == 0
        
        # In strict mode, any detection means not clean
        if self.strict_mode and (filtered_words_found or filtered_phrases_found):
            is_clean = False
        
        return {
            'is_clean': is_clean,
            'filtered_words_found': filtered_words_found,
            'filtered_phrases_found': filtered_phrases_found,
            'analysis_confidence': 'high',
            'strict_mode': self.strict_mode
        }
    
    def _move_file(self, channel: int, source_path: str, is_clean: bool) -> Optional[str]:
        """
        Move audio file to appropriate directory based on filter results.
        
        Args:
            channel: Audio channel (1-5)
            source_path: Source file path
            is_clean: Whether content passed filter
            
        Returns:
            Destination file path, or None if move failed
        """
        try:
            if not os.path.exists(source_path):
                self.logger.error(f"Source file not found: {source_path}")
                return None
            
            # Determine destination directory
            if is_clean:
                dest_dir = os.path.join(self.playable_dir, f"channel_{channel}")
            else:
                dest_dir = os.path.join(self.bin_dir, f"channel_{channel}")
            
            # Generate destination file name
            filename = os.path.basename(source_path)
            dest_path = os.path.join(dest_dir, filename)
            
            # Handle filename conflicts
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(dest_path):
                    new_filename = f"{base}_{counter}{ext}"
                    dest_path = os.path.join(dest_dir, new_filename)
                    counter += 1
            
            # Move file
            shutil.move(source_path, dest_path)
            
            self.logger.info(f"Moved file from {source_path} to {dest_path}")
            return dest_path
            
        except Exception as e:
            self.logger.error(f"Failed to move file {source_path}: {e}")
            return None
    
    def get_channel_files(self, channel: int, clean_only: bool = True) -> List[str]:
        """
        Get list of files for specified channel.
        
        Args:
            channel: Channel number (1-5)
            clean_only: If True, only return clean (playable) files
            
        Returns:
            List of file paths
        """
        try:
            files = []
            
            if clean_only:
                # Get files from playable directory
                playable_channel_dir = os.path.join(self.playable_dir, f"channel_{channel}")
                if os.path.exists(playable_channel_dir):
                    for filename in os.listdir(playable_channel_dir):
                        if filename.endswith('.wav'):
                            files.append(os.path.join(playable_channel_dir, filename))
            else:
                # Get files from both directories
                for base_dir in [self.playable_dir, self.bin_dir]:
                    channel_dir = os.path.join(base_dir, f"channel_{channel}")
                    if os.path.exists(channel_dir):
                        for filename in os.listdir(channel_dir):
                            if filename.endswith('.wav'):
                                files.append(os.path.join(channel_dir, filename))
            
            # Sort by modification time (newest first)
            files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return files
            
        except Exception as e:
            self.logger.error(f"Failed to get channel {channel} files: {e}")
            return []
    
    def get_latest_playable_file(self, channel: int) -> Optional[str]:
        """
        Get the most recent playable file for specified channel.
        
        Args:
            channel: Channel number (1-5)
            
        Returns:
            Path to latest playable file, or None if none available
        """
        files = self.get_channel_files(channel, clean_only=True)
        return files[0] if files else None
    
    def add_filtered_word(self, word: str):
        """
        Add word to filtered word list.
        
        Args:
            word: Word to add to filter list
        """
        if not self.case_sensitive:
            word = word.lower()
        
        self.filtered_words.add(word.strip())
        self.logger.info(f"Added filtered word: {word}")
    
    def remove_filtered_word(self, word: str):
        """
        Remove word from filtered word list.
        
        Args:
            word: Word to remove from filter list
        """
        if not self.case_sensitive:
            word = word.lower()
        
        self.filtered_words.discard(word.strip())
        self.logger.info(f"Removed filtered word: {word}")
    
    def get_filter_stats(self) -> Dict:
        """
        Get content filter statistics.
        
        Returns:
            Dictionary with filter statistics
        """
        stats = {
            'filtered_words_count': len(self.filtered_words),
            'filtered_phrases_count': len(self.filtered_phrases),
            'strict_mode': self.strict_mode,
            'case_sensitive': self.case_sensitive
        }
        
        # Get file counts per channel
        for channel in range(1, 6):
            playable_files = len(self.get_channel_files(channel, clean_only=True))
            all_files = len(self.get_channel_files(channel, clean_only=False))
            filtered_files = all_files - playable_files
            
            stats[f'channel_{channel}'] = {
                'playable_files': playable_files,
                'filtered_files': filtered_files,
                'total_files': all_files
            }
        
        return stats
    
    def cleanup(self):
        """Clean up content filter resources."""
        try:
            self.logger.info("Content filter cleanup completed")
        except Exception as e:
            self.logger.error(f"Content filter cleanup failed: {e}")