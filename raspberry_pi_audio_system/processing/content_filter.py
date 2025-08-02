"""
Content Filter for Raspberry Pi Audio System.

Filters processed speech transcripts for inappropriate content and
determines file placement (bin vs playable folder).

Multi-level filtering system with:
- Configurable word/phrase filtering with pattern matching
- Quality assessment with intelligibility scoring  
- Channel-aware processing and file routing
- Comprehensive metadata generation and audit trails
- Integration with speech processor for real-time filtering
- Performance optimizations for Raspberry Pi
"""

import logging
import re
import json
import time
import threading
from typing import List, Dict, Optional, Set, Callable, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil
import statistics
from enum import Enum


class FilterMode(Enum):
    """Content filter modes."""
    STRICT = "strict"
    MODERATE = "moderate" 
    PERMISSIVE = "permissive"
    CUSTOM = "custom"
    EMERGENCY = "emergency"
    MAINTENANCE = "maintenance"


class FilterCategory(Enum):
    """Filter categories for different types of content."""
    PROFANITY = "profanity"
    SENSITIVE = "sensitive"
    CUSTOM = "custom"
    QUALITY = "quality"
    LENGTH = "length"
    CONFIDENCE = "confidence"


class ContentFilter:
    """
    Content filter for speech transcript processing.
    
    Evaluates transcripts against filtered word lists and determines
    appropriate file placement and handling with multi-level filtering.
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
        
        # Filter modes and settings
        self.filter_mode = FilterMode(self.filter_config.get('mode', 'strict'))
        self.case_sensitive = self.filter_config.get('case_sensitive', False)
        self.emergency_bypass = False
        self.maintenance_mode = False
        
        # Load filtered content by category
        self.filtered_content: Dict[FilterCategory, Set[str]] = {
            FilterCategory.PROFANITY: set(),
            FilterCategory.SENSITIVE: set(), 
            FilterCategory.CUSTOM: set()
        }
        self.filtered_phrases: Dict[FilterCategory, Set[str]] = {
            FilterCategory.PROFANITY: set(),
            FilterCategory.SENSITIVE: set(),
            FilterCategory.CUSTOM: set()
        }
        self.regex_patterns: Dict[FilterCategory, List[re.Pattern]] = {
            FilterCategory.PROFANITY: [],
            FilterCategory.SENSITIVE: [],
            FilterCategory.CUSTOM: []
        }
        
        # Quality assessment settings
        self.quality_config = self.filter_config.get('quality_assessment', {})
        self.min_transcript_length = self.quality_config.get('min_length', 3)
        self.max_transcript_length = self.quality_config.get('max_length', 1000)
        self.min_confidence_threshold = self.quality_config.get('min_confidence', 0.5)
        self.intelligibility_threshold = self.quality_config.get('intelligibility_threshold', 0.6)
        
        # Channel-specific overrides
        self.channel_overrides = self.filter_config.get('channel_overrides', {})
        
        # Load filtered content
        self._load_filtered_content()
        
        # File paths
        self.bin_dir = config.get('paths', {}).get('bin', './bin')
        self.playable_dir = config.get('paths', {}).get('playable', './playable')
        self.recordings_dir = config.get('paths', {}).get('recordings', './recordings')
        self.transcripts_dir = config.get('paths', {}).get('transcripts', './transcripts')
        
        # Statistics and audit trail
        self.filter_stats = {
            'total_processed': 0,
            'total_filtered': 0,
            'total_accepted': 0,
            'by_channel': {f'channel_{i}': {'processed': 0, 'filtered': 0, 'accepted': 0} for i in range(1, 6)},
            'by_category': {cat.value: 0 for cat in FilterCategory},
            'start_time': datetime.now()
        }
        
        # Threading and performance
        self.processing_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        
        # Callbacks
        self.filter_callback: Optional[Callable] = None
        self.completion_callbacks: List[Callable] = []
        
        # Ensure directories exist
        self._create_directories()
        
        # Create audit log
        self._setup_audit_logging()
    
    def _load_filtered_content(self):
        """Load filtered words, phrases, and patterns from configuration."""
        try:
            # Load basic filtered words (legacy support)
            filtered_words = self.filter_config.get('filtered_words', [])
            for word in filtered_words:
                processed_word = word.lower() if not self.case_sensitive else word
                self.filtered_content[FilterCategory.PROFANITY].add(processed_word.strip())
            
            # Load basic filtered phrases (legacy support)
            filtered_phrases = self.filter_config.get('filtered_phrases', [])
            for phrase in filtered_phrases:
                processed_phrase = phrase.lower() if not self.case_sensitive else phrase
                self.filtered_phrases[FilterCategory.PROFANITY].add(processed_phrase.strip())
            
            # Load categorized content
            categories_config = self.filter_config.get('categories', {})
            for category_name, category_config in categories_config.items():
                try:
                    category = FilterCategory(category_name)
                except ValueError:
                    self.logger.warning(f"Unknown filter category: {category_name}")
                    continue
                
                # Load words for this category
                words = category_config.get('words', [])
                for word in words:
                    processed_word = word.lower() if not self.case_sensitive else word
                    self.filtered_content[category].add(processed_word.strip())
                
                # Load phrases for this category
                phrases = category_config.get('phrases', [])
                for phrase in phrases:
                    processed_phrase = phrase.lower() if not self.case_sensitive else phrase
                    self.filtered_phrases[category].add(processed_phrase.strip())
                
                # Load regex patterns for this category
                patterns = category_config.get('patterns', [])
                for pattern in patterns:
                    try:
                        flags = re.IGNORECASE if not self.case_sensitive else 0
                        compiled_pattern = re.compile(pattern, flags)
                        self.regex_patterns[category].append(compiled_pattern)
                    except re.error as e:
                        self.logger.error(f"Invalid regex pattern '{pattern}': {e}")
            
            # Log loaded content
            total_words = sum(len(words) for words in self.filtered_content.values())
            total_phrases = sum(len(phrases) for phrases in self.filtered_phrases.values())
            total_patterns = sum(len(patterns) for patterns in self.regex_patterns.values())
            
            self.logger.info(f"Loaded filtering content: {total_words} words, {total_phrases} phrases, {total_patterns} patterns")
            
        except Exception as e:
            self.logger.error(f"Failed to load filtered content: {e}")
    
    def _create_directories(self):
        """Create necessary directories for file management."""
        try:
            # Create main directories
            for directory in [self.bin_dir, self.playable_dir]:
                os.makedirs(directory, exist_ok=True)
                
                # Create channel subdirectories with organized structure
                for channel in range(1, 6):
                    channel_dir = os.path.join(directory, f"channel_{channel}")
                    os.makedirs(channel_dir, exist_ok=True)
                    
                    # Create subdirectories for organized storage
                    for subdir in ['audio', 'transcripts', 'metadata']:
                        subdir_path = os.path.join(channel_dir, subdir)
                        os.makedirs(subdir_path, exist_ok=True)
                    
                    # For bin directory, also create filtered reasons directory
                    if directory == self.bin_dir:
                        filtered_reasons_dir = os.path.join(channel_dir, 'filtered_reasons')
                        os.makedirs(filtered_reasons_dir, exist_ok=True)
            
            self.logger.info(f"Created filter directories: {self.bin_dir}, {self.playable_dir}")
            
        except Exception as e:
            self.logger.error(f"Failed to create filter directories: {e}")
            raise
    
    def _setup_audit_logging(self):
        """Setup audit logging for filter decisions."""
        try:
            logs_dir = self.config.get('paths', {}).get('logs', './logs')
            os.makedirs(logs_dir, exist_ok=True)
            
            # Create audit log file
            self.audit_log_path = os.path.join(logs_dir, 'content_filter_audit.log')
            
            # Setup audit logger
            self.audit_logger = logging.getLogger('content_filter_audit')
            self.audit_logger.setLevel(logging.INFO)
            
            # Create file handler for audit log
            if not self.audit_logger.handlers:
                handler = logging.FileHandler(self.audit_log_path)
                formatter = logging.Formatter('%(asctime)s - %(message)s')
                handler.setFormatter(formatter)
                self.audit_logger.addHandler(handler)
            
            self.logger.info(f"Audit logging setup complete: {self.audit_log_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to setup audit logging: {e}")
    
    def set_filter_callback(self, callback: Callable):
        """
        Set callback function for filter processing completion.
        
        Args:
            callback: Function to call when filtering is complete
                     Signature: callback(channel, file_path, is_clean, filter_results)
        """
        self.filter_callback = callback
        self.logger.info("Content filter callback registered")
    
    def register_completion_callback(self, callback: Callable):
        """
        Register callback for filtering completion (for Queue Manager integration).
        
        Args:
            callback: Function to call when filtering is complete
                     Signature: callback(channel, file_path, is_clean, metadata)
        """
        self.completion_callbacks.append(callback)
        self.logger.info("Completion callback registered")
    
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
            with self.processing_lock:
                start_time = time.time()
                
                self.logger.info(f"Processing transcript for channel {channel}: '{transcript[:50]}...'")
                
                # Check for emergency bypass or maintenance mode
                if self.emergency_bypass:
                    return self._emergency_bypass_processing(channel, audio_file_path, transcript, confidence, metadata)
                
                if self.maintenance_mode:
                    return self._maintenance_mode_processing(channel, audio_file_path, transcript, confidence, metadata)
                
                # Perform comprehensive content analysis
                filter_results = self._comprehensive_content_analysis(transcript, confidence, channel)
                
                # Determine if content is acceptable
                is_acceptable = self._determine_acceptability(filter_results, channel)
                
                # Route files to appropriate directories
                destination_paths = self._route_files(channel, audio_file_path, transcript, is_acceptable, filter_results)
                
                # Generate comprehensive metadata
                comprehensive_metadata = self._generate_metadata(
                    channel, audio_file_path, transcript, confidence, 
                    filter_results, is_acceptable, destination_paths, metadata, start_time
                )
                
                # Update statistics
                self._update_statistics(channel, is_acceptable, filter_results)
                
                # Log to audit trail
                self._log_audit_trail(comprehensive_metadata)
                
                # Call completion callbacks
                self._call_completion_callbacks(channel, destination_paths, is_acceptable, comprehensive_metadata)
                
                # Legacy callback support
                if self.filter_callback:
                    try:
                        self.filter_callback(channel, destination_paths.get('audio'), is_acceptable, comprehensive_metadata)
                    except Exception as e:
                        self.logger.error(f"Error in filter callback: {e}")
                
                processing_time = time.time() - start_time
                status = "ACCEPTED" if is_acceptable else "FILTERED"
                self.logger.info(f"Channel {channel} content {status} in {processing_time:.3f}s")
                
                return comprehensive_metadata
                
        except Exception as e:
            self.logger.error(f"Content filter processing failed for channel {channel}: {e}")
            return self._create_error_result(channel, audio_file_path, str(e))
    
    def _comprehensive_content_analysis(self, transcript: str, confidence: float, channel: int) -> Dict:
        """
        Perform comprehensive content analysis with multi-level filtering.
        
        Args:
            transcript: Speech transcript to analyze
            confidence: Transcript confidence score
            channel: Audio channel number
            
        Returns:
            Comprehensive analysis results dictionary
        """
        if not transcript:
            return {
                'quality_assessment': self._assess_quality("", confidence),
                'content_filters': {cat.value: {'words': [], 'phrases': [], 'patterns': []} for cat in FilterCategory if cat in [FilterCategory.PROFANITY, FilterCategory.SENSITIVE, FilterCategory.CUSTOM]},
                'overall_score': 1.0,
                'analysis_confidence': 'high'
            }
        
        # Prepare text for analysis
        analysis_text = transcript
        if not self.case_sensitive:
            analysis_text = transcript.lower()
        
        # Quality assessment
        quality_assessment = self._assess_quality(transcript, confidence)
        
        # Content filtering by category
        content_filters = {}
        for category in [FilterCategory.PROFANITY, FilterCategory.SENSITIVE, FilterCategory.CUSTOM]:
            content_filters[category.value] = self._filter_by_category(analysis_text, category)
        
        # Channel-specific filtering
        channel_specific_results = self._apply_channel_specific_filtering(analysis_text, channel)
        
        # Calculate overall score
        overall_score = self._calculate_overall_score(quality_assessment, content_filters, channel_specific_results)
        
        # Determine analysis confidence
        analysis_confidence = self._determine_analysis_confidence(transcript, confidence, content_filters)
        
        return {
            'quality_assessment': quality_assessment,
            'content_filters': content_filters,
            'channel_specific': channel_specific_results,
            'overall_score': overall_score,
            'analysis_confidence': analysis_confidence,
            'filter_mode': self.filter_mode.value,
            'processing_time': datetime.now().isoformat()
        }
    
    def _assess_quality(self, transcript: str, confidence: float) -> Dict:
        """
        Assess transcript quality based on multiple factors.
        
        Args:
            transcript: Transcript text
            confidence: STT confidence score
            
        Returns:
            Quality assessment dictionary
        """
        assessment = {
            'length_check': {'passed': True, 'score': 1.0, 'details': ''},
            'confidence_check': {'passed': True, 'score': 1.0, 'details': ''},
            'intelligibility_check': {'passed': True, 'score': 1.0, 'details': ''},
            'coherence_check': {'passed': True, 'score': 1.0, 'details': ''},
            'overall_quality_score': 1.0
        }
        
        if not transcript:
            assessment.update({
                'length_check': {'passed': False, 'score': 0.0, 'details': 'Empty transcript'},
                'overall_quality_score': 0.0
            })
            return assessment
        
        # Length assessment
        length = len(transcript.strip())
        word_count = len(transcript.split())
        
        if length < self.min_transcript_length:
            assessment['length_check'] = {
                'passed': False, 
                'score': length / self.min_transcript_length,
                'details': f'Too short: {length} chars < {self.min_transcript_length}'
            }
        elif length > self.max_transcript_length:
            assessment['length_check'] = {
                'passed': False,
                'score': self.max_transcript_length / length,
                'details': f'Too long: {length} chars > {self.max_transcript_length}'
            }
        else:
            # Good length, score based on optimal range
            optimal_length = (self.min_transcript_length + min(self.max_transcript_length, 200)) / 2
            score = 1.0 - abs(length - optimal_length) / optimal_length * 0.2
            assessment['length_check'] = {
                'passed': True,
                'score': max(0.8, score),
                'details': f'Good length: {length} characters, {word_count} words'
            }
        
        # Confidence assessment
        if confidence < self.min_confidence_threshold:
            assessment['confidence_check'] = {
                'passed': False,
                'score': confidence / self.min_confidence_threshold,
                'details': f'Low confidence: {confidence:.2f} < {self.min_confidence_threshold}'
            }
        else:
            assessment['confidence_check'] = {
                'passed': True,
                'score': confidence,
                'details': f'Good confidence: {confidence:.2f}'
            }
        
        # Intelligibility assessment
        intelligibility_score = self._calculate_intelligibility(transcript)
        if intelligibility_score < self.intelligibility_threshold:
            assessment['intelligibility_check'] = {
                'passed': False,
                'score': intelligibility_score,
                'details': f'Low intelligibility: {intelligibility_score:.2f} < {self.intelligibility_threshold}'
            }
        else:
            assessment['intelligibility_check'] = {
                'passed': True,
                'score': intelligibility_score,
                'details': f'Good intelligibility: {intelligibility_score:.2f}'
            }
        
        # Coherence assessment
        coherence_score = self._calculate_coherence(transcript)
        assessment['coherence_check'] = {
            'passed': coherence_score >= 0.6,
            'score': coherence_score,
            'details': f'Coherence score: {coherence_score:.2f}'
        }
        
        # Calculate overall quality score
        scores = [
            assessment['length_check']['score'],
            assessment['confidence_check']['score'],
            assessment['intelligibility_check']['score'],
            assessment['coherence_check']['score']
        ]
        assessment['overall_quality_score'] = statistics.mean(scores)
        
        return assessment
    
    def _calculate_intelligibility(self, transcript: str) -> float:
        """
        Calculate intelligibility score based on text characteristics.
        
        Args:
            transcript: Transcript text
            
        Returns:
            Intelligibility score between 0.0 and 1.0
        """
        if not transcript:
            return 0.0
        
        try:
            # Base score
            score = 0.7
            
            # Word characteristics
            words = transcript.split()
            if not words:
                return 0.0
            
            # Average word length (optimal range 3-7 characters)
            avg_word_length = sum(len(word) for word in words) / len(words)
            if 3 <= avg_word_length <= 7:
                score += 0.1
            else:
                score -= 0.1
            
            # Proportion of very short words (could indicate unclear speech)
            short_words = sum(1 for word in words if len(word) <= 2)
            short_word_ratio = short_words / len(words)
            if short_word_ratio > 0.3:
                score -= short_word_ratio * 0.2
            
            # Proportion of very long words (could indicate artifacts)
            long_words = sum(1 for word in words if len(word) > 10)
            long_word_ratio = long_words / len(words)
            if long_word_ratio > 0.1:
                score -= long_word_ratio * 0.3
            
            # Character variety (repetitive characters might indicate poor recognition)
            unique_chars = len(set(transcript.lower()))
            char_variety = unique_chars / max(1, len(transcript))
            if char_variety < 0.1:
                score -= 0.2
            
            # Punctuation density (too much might indicate unclear speech)
            punctuation_count = sum(1 for char in transcript if char in '.,!?;:-')
            punctuation_density = punctuation_count / len(transcript)
            if punctuation_density > 0.1:
                score -= punctuation_density * 0.5
            
            return max(0.0, min(1.0, score))
            
        except Exception:
            return 0.5
    
    def _calculate_coherence(self, transcript: str) -> float:
        """
        Calculate coherence score based on text structure.
        
        Args:
            transcript: Transcript text
            
        Returns:
            Coherence score between 0.0 and 1.0
        """
        if not transcript:
            return 0.0
        
        try:
            # Base score
            score = 0.6
            
            words = transcript.split()
            if len(words) < 2:
                return 0.3
            
            # Sentence structure indicators
            sentences = re.split(r'[.!?]+', transcript)
            sentences = [s.strip() for s in sentences if s.strip()]
            
            if sentences:
                # Average sentence length (optimal range 5-15 words)
                avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences)
                if 5 <= avg_sentence_length <= 15:
                    score += 0.2
                elif avg_sentence_length < 3:
                    score -= 0.2
                
                # Sentence count relative to total words
                sentence_ratio = len(sentences) / len(words)
                if 0.1 <= sentence_ratio <= 0.3:
                    score += 0.1
            
            # Word repetition (excessive repetition reduces coherence)
            word_counts = {}
            for word in words:
                word_lower = word.lower()
                word_counts[word_lower] = word_counts.get(word_lower, 0) + 1
            
            if words:
                max_repetition = max(word_counts.values())
                repetition_ratio = max_repetition / len(words)
                if repetition_ratio > 0.3:
                    score -= repetition_ratio * 0.3
            
            # Basic grammar indicators (presence of common function words)
            function_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with'}
            function_word_count = sum(1 for word in words if word.lower() in function_words)
            function_word_ratio = function_word_count / len(words) if words else 0
            
            if 0.1 <= function_word_ratio <= 0.4:
                score += 0.1
            
            return max(0.0, min(1.0, score))
            
        except Exception:
            return 0.5
    
    def _filter_by_category(self, text: str, category: FilterCategory) -> Dict:
        """
        Filter text by specific category.
        
        Args:
            text: Text to filter
            category: Filter category
            
        Returns:
            Filter results for this category
        """
        results = {
            'words': [],
            'phrases': [],
            'patterns': [],
            'total_hits': 0
        }
        
        if not text:
            return results
        
        # Check words
        for word in self.filtered_content.get(category, set()):
            pattern = r'\b' + re.escape(word) + r'\b'
            flags = re.IGNORECASE if not self.case_sensitive else 0
            if re.search(pattern, text, flags):
                results['words'].append(word)
                results['total_hits'] += 1
        
        # Check phrases
        for phrase in self.filtered_phrases.get(category, set()):
            if phrase in text:
                results['phrases'].append(phrase)
                results['total_hits'] += 1
        
        # Check regex patterns
        for pattern in self.regex_patterns.get(category, []):
            matches = pattern.findall(text)
            if matches:
                results['patterns'].extend(matches)
                results['total_hits'] += len(matches)
        
        return results
    
    def _apply_channel_specific_filtering(self, text: str, channel: int) -> Dict:
        """
        Apply channel-specific filtering rules.
        
        Args:
            text: Text to filter
            channel: Channel number
            
        Returns:
            Channel-specific filter results
        """
        channel_key = f'channel_{channel}'
        channel_config = self.channel_overrides.get(channel_key, {})
        
        results = {
            'mode_override': None,
            'additional_filters': [],
            'bypass_filters': [],
            'custom_rules_applied': []
        }
        
        # Check for mode override
        if 'mode' in channel_config:
            results['mode_override'] = channel_config['mode']
        
        # Apply additional filters specific to this channel
        additional_words = channel_config.get('additional_words', [])
        for word in additional_words:
            word_check = word.lower() if not self.case_sensitive else word
            pattern = r'\b' + re.escape(word_check) + r'\b'
            flags = re.IGNORECASE if not self.case_sensitive else 0
            if re.search(pattern, text, flags):
                results['additional_filters'].append(word)
        
        # Check for bypass filters (words that should be ignored for this channel)
        bypass_words = channel_config.get('bypass_words', [])
        results['bypass_filters'] = bypass_words
        
        # Apply custom rules
        custom_rules = channel_config.get('custom_rules', [])
        for rule in custom_rules:
            try:
                pattern = rule.get('pattern', '')
                if pattern and re.search(pattern, text, re.IGNORECASE):
                    results['custom_rules_applied'].append(rule)
            except re.error:
                continue
        
        return results
    
    def _calculate_overall_score(self, quality_assessment: Dict, content_filters: Dict, channel_specific: Dict) -> float:
        """
        Calculate overall acceptability score.
        
        Args:
            quality_assessment: Quality assessment results
            content_filters: Content filter results
            channel_specific: Channel-specific results
            
        Returns:
            Overall score between 0.0 and 1.0
        """
        try:
            # Start with quality score
            base_score = quality_assessment.get('overall_quality_score', 0.5)
            
            # Apply content filter penalties
            content_penalty = 0.0
            for category, results in content_filters.items():
                hits = results.get('total_hits', 0)
                if hits > 0:
                    # Different penalties for different categories
                    if category == 'profanity':
                        content_penalty += hits * 0.3
                    elif category == 'sensitive':
                        content_penalty += hits * 0.2
                    elif category == 'custom':
                        content_penalty += hits * 0.25
            
            # Apply channel-specific adjustments
            channel_penalty = len(channel_specific.get('additional_filters', [])) * 0.2
            channel_bonus = len(channel_specific.get('bypass_filters', [])) * 0.1
            
            # Calculate final score
            final_score = base_score - content_penalty - channel_penalty + channel_bonus
            
            return max(0.0, min(1.0, final_score))
            
        except Exception:
            return 0.5
    
    def _determine_analysis_confidence(self, transcript: str, stt_confidence: float, content_filters: Dict) -> str:
        """
        Determine confidence level of the analysis.
        
        Args:
            transcript: Original transcript
            stt_confidence: STT confidence score
            content_filters: Content filter results
            
        Returns:
            Confidence level: 'high', 'medium', 'low'
        """
        try:
            # Start with STT confidence
            confidence_score = stt_confidence
            
            # Adjust based on transcript characteristics
            if len(transcript) < 10:
                confidence_score -= 0.2
            elif len(transcript) > 100:
                confidence_score += 0.1
            
            # Adjust based on filter hits (clear violations are high confidence)
            total_hits = sum(results.get('total_hits', 0) for results in content_filters.values())
            if total_hits > 0:
                confidence_score += 0.1
            
            # Categorize confidence
            if confidence_score >= 0.8:
                return 'high'
            elif confidence_score >= 0.6:
                return 'medium'
            else:
                return 'low'
                
        except Exception:
            return 'medium'
    
    def _determine_acceptability(self, filter_results: Dict, channel: int) -> bool:
        """
        Determine if content is acceptable based on filter results and mode.
        
        Args:
            filter_results: Comprehensive filter results
            channel: Channel number
            
        Returns:
            True if content is acceptable
        """
        try:
            # Check channel-specific mode override
            channel_specific = filter_results.get('channel_specific', {})
            mode_override = channel_specific.get('mode_override')
            current_mode = FilterMode(mode_override) if mode_override else self.filter_mode
            
            overall_score = filter_results.get('overall_score', 0.5)
            quality_assessment = filter_results.get('quality_assessment', {})
            content_filters = filter_results.get('content_filters', {})
            
            # Emergency mode - accept everything
            if current_mode == FilterMode.EMERGENCY:
                return True
            
            # Maintenance mode - accept everything
            if current_mode == FilterMode.MAINTENANCE:
                return True
            
            # Quality thresholds
            quality_score = quality_assessment.get('overall_quality_score', 0.0)
            if quality_score < 0.3:  # Very poor quality
                return False
            
            # Content filter evaluation based on mode
            total_content_hits = sum(results.get('total_hits', 0) for results in content_filters.values())
            
            if current_mode == FilterMode.STRICT:
                # Any content violation or low quality fails
                if total_content_hits > 0 or overall_score < 0.7:
                    return False
            
            elif current_mode == FilterMode.MODERATE:
                # Multiple violations or severe single violation fails
                profanity_hits = content_filters.get('profanity', {}).get('total_hits', 0)
                sensitive_hits = content_filters.get('sensitive', {}).get('total_hits', 0)
                
                if profanity_hits > 0 or sensitive_hits > 1 or overall_score < 0.5:
                    return False
            
            elif current_mode == FilterMode.PERMISSIVE:
                # Only severe violations fail
                if overall_score < 0.3 or total_content_hits > 3:
                    return False
            
            elif current_mode == FilterMode.CUSTOM:
                # Use overall score threshold
                threshold = self.filter_config.get('custom_threshold', 0.6)
                if overall_score < threshold:
                    return False
            
            # Additional channel-specific checks
            additional_filters = channel_specific.get('additional_filters', [])
            if additional_filters and current_mode == FilterMode.STRICT:
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error determining acceptability: {e}")
            # Default to rejection on error in strict mode
            return self.filter_mode != FilterMode.STRICT
    
    def _route_files(self, channel: int, audio_file_path: str, transcript: str, 
                    is_acceptable: bool, filter_results: Dict) -> Dict[str, str]:
        """
        Route files to appropriate directories based on filter results.
        
        Args:
            channel: Channel number
            audio_file_path: Original audio file path
            transcript: Transcript text
            is_acceptable: Whether content passed filtering
            filter_results: Complete filter results
            
        Returns:
            Dictionary of destination paths
        """
        try:
            destination_paths = {}
            
            # Determine base destination directory
            if is_acceptable:
                base_dir = os.path.join(self.playable_dir, f"channel_{channel}")
            else:
                base_dir = os.path.join(self.bin_dir, f"channel_{channel}")
            
            # Generate file basename
            audio_basename = os.path.splitext(os.path.basename(audio_file_path))[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_filename = f"{audio_basename}_{timestamp}"
            
            # Route audio file
            audio_dest_dir = os.path.join(base_dir, "audio")
            audio_dest_path = os.path.join(audio_dest_dir, f"{base_filename}.wav")
            
            # Handle filename conflicts
            counter = 1
            while os.path.exists(audio_dest_path):
                audio_dest_path = os.path.join(audio_dest_dir, f"{base_filename}_{counter}.wav")
                counter += 1
            
            # Move audio file
            if os.path.exists(audio_file_path):
                shutil.move(audio_file_path, audio_dest_path)
                destination_paths['audio'] = audio_dest_path
                self.logger.info(f"Moved audio file to: {audio_dest_path}")
            
            # Create transcript file
            transcript_dest_dir = os.path.join(base_dir, "transcripts")
            transcript_dest_path = os.path.join(transcript_dest_dir, f"{os.path.basename(audio_dest_path).replace('.wav', '.txt')}")
            
            with open(transcript_dest_path, 'w', encoding='utf-8') as f:
                f.write(transcript)
            destination_paths['transcript'] = transcript_dest_path
            
            # Create metadata file
            metadata_dest_dir = os.path.join(base_dir, "metadata")
            metadata_dest_path = os.path.join(metadata_dest_dir, f"{os.path.basename(audio_dest_path).replace('.wav', '_metadata.json')}")
            destination_paths['metadata'] = metadata_dest_path
            
            # For filtered content, also create filtered reasons file
            if not is_acceptable:
                reasons_dest_dir = os.path.join(base_dir, "filtered_reasons")
                reasons_dest_path = os.path.join(reasons_dest_dir, f"{os.path.basename(audio_dest_path).replace('.wav', '_reasons.json')}")
                
                reasons_data = {
                    'filter_results': filter_results,
                    'timestamp': datetime.now().isoformat(),
                    'channel': channel,
                    'original_file': audio_file_path
                }
                
                with open(reasons_dest_path, 'w', encoding='utf-8') as f:
                    json.dump(reasons_data, f, indent=2)
                destination_paths['reasons'] = reasons_dest_path
            
            return destination_paths
            
        except Exception as e:
            self.logger.error(f"Failed to route files for channel {channel}: {e}")
            return {}
    
    def _generate_metadata(self, channel: int, audio_file_path: str, transcript: str, 
                          confidence: float, filter_results: Dict, is_acceptable: bool,
                          destination_paths: Dict, original_metadata: Dict, start_time: float) -> Dict:
        """
        Generate comprehensive metadata for filtered content.
        
        Args:
            channel: Channel number
            audio_file_path: Original audio file path
            transcript: Transcript text
            confidence: STT confidence score
            filter_results: Filter analysis results
            is_acceptable: Whether content was accepted
            destination_paths: File destination paths
            original_metadata: Original processing metadata
            start_time: Processing start time
            
        Returns:
            Comprehensive metadata dictionary
        """
        processing_time = time.time() - start_time
        
        metadata = {
            # Basic information
            'channel': channel,
            'timestamp': datetime.now().isoformat(),
            'processing_time_seconds': processing_time,
            
            # File information
            'original_audio_file': audio_file_path,
            'destination_paths': destination_paths,
            'transcript': transcript,
            'transcript_length': len(transcript),
            'word_count': len(transcript.split()) if transcript else 0,
            
            # Processing results
            'is_acceptable': is_acceptable,
            'stt_confidence': confidence,
            'filter_results': filter_results,
            'filter_mode': self.filter_mode.value,
            
            # System information
            'processor_version': '1.0.0',
            'emergency_bypass': self.emergency_bypass,
            'maintenance_mode': self.maintenance_mode,
            
            # Original metadata
            'original_metadata': original_metadata
        }
        
        # Save metadata file
        if 'metadata' in destination_paths:
            try:
                with open(destination_paths['metadata'], 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                self.logger.error(f"Failed to save metadata file: {e}")
        
        return metadata
    
    def _update_statistics(self, channel: int, is_acceptable: bool, filter_results: Dict):
        """
        Update filtering statistics.
        
        Args:
            channel: Channel number
            is_acceptable: Whether content was accepted
            filter_results: Filter results
        """
        try:
            with self.stats_lock:
                # Update totals
                self.filter_stats['total_processed'] += 1
                if is_acceptable:
                    self.filter_stats['total_accepted'] += 1
                else:
                    self.filter_stats['total_filtered'] += 1
                
                # Update channel stats
                channel_key = f'channel_{channel}'
                if channel_key in self.filter_stats['by_channel']:
                    self.filter_stats['by_channel'][channel_key]['processed'] += 1
                    if is_acceptable:
                        self.filter_stats['by_channel'][channel_key]['accepted'] += 1
                    else:
                        self.filter_stats['by_channel'][channel_key]['filtered'] += 1
                
                # Update category stats
                content_filters = filter_results.get('content_filters', {})
                for category, results in content_filters.items():
                    hits = results.get('total_hits', 0)
                    if hits > 0:
                        self.filter_stats['by_category'][category] += hits
                        
        except Exception as e:
            self.logger.error(f"Failed to update statistics: {e}")
    
    def _log_audit_trail(self, metadata: Dict):
        """
        Log filtering decision to audit trail.
        
        Args:
            metadata: Complete processing metadata
        """
        try:
            audit_entry = {
                'timestamp': metadata['timestamp'],
                'channel': metadata['channel'],
                'decision': 'ACCEPTED' if metadata['is_acceptable'] else 'FILTERED',
                'transcript_preview': metadata['transcript'][:100] + '...' if len(metadata['transcript']) > 100 else metadata['transcript'],
                'filter_mode': metadata['filter_mode'],
                'overall_score': metadata['filter_results'].get('overall_score', 0.0),
                'processing_time': metadata['processing_time_seconds']
            }
            
            # Add filter hit summary for rejected content
            if not metadata['is_acceptable']:
                content_filters = metadata['filter_results'].get('content_filters', {})
                hit_summary = {}
                for category, results in content_filters.items():
                    hits = results.get('total_hits', 0)
                    if hits > 0:
                        hit_summary[category] = {
                            'hits': hits,
                            'words': len(results.get('words', [])),
                            'phrases': len(results.get('phrases', [])),
                            'patterns': len(results.get('patterns', []))
                        }
                audit_entry['filter_hits'] = hit_summary
            
            self.audit_logger.info(json.dumps(audit_entry))
            
        except Exception as e:
            self.logger.error(f"Failed to log audit trail: {e}")
    
    def _call_completion_callbacks(self, channel: int, destination_paths: Dict, 
                                  is_acceptable: bool, metadata: Dict):
        """
        Call all registered completion callbacks.
        
        Args:
            channel: Channel number
            destination_paths: File destination paths
            is_acceptable: Whether content was accepted
            metadata: Complete metadata
        """
        for callback in self.completion_callbacks:
            try:
                callback(channel, destination_paths.get('audio'), is_acceptable, metadata)
            except Exception as e:
                self.logger.error(f"Error in completion callback: {e}")
    
    def _emergency_bypass_processing(self, channel: int, audio_file_path: str, 
                                   transcript: str, confidence: float, metadata: Dict) -> Dict:
        """
        Process content in emergency bypass mode (accept everything).
        
        Args:
            channel: Channel number
            audio_file_path: Audio file path
            transcript: Transcript text
            confidence: STT confidence
            metadata: Original metadata
            
        Returns:
            Emergency processing results
        """
        try:
            # Route to playable directory
            destination_paths = self._route_files(channel, audio_file_path, transcript, True, {})
            
            emergency_metadata = {
                'channel': channel,
                'timestamp': datetime.now().isoformat(),
                'is_acceptable': True,
                'emergency_bypass': True,
                'transcript': transcript,
                'stt_confidence': confidence,
                'destination_paths': destination_paths,
                'original_metadata': metadata,
                'filter_results': {'emergency_mode': True}
            }
            
            # Update stats
            with self.stats_lock:
                self.filter_stats['total_processed'] += 1
                self.filter_stats['total_accepted'] += 1
            
            self.logger.warning(f"Emergency bypass: Channel {channel} content accepted without filtering")
            return emergency_metadata
            
        except Exception as e:
            self.logger.error(f"Emergency bypass processing failed: {e}")
            return self._create_error_result(channel, audio_file_path, str(e))
    
    def _maintenance_mode_processing(self, channel: int, audio_file_path: str,
                                   transcript: str, confidence: float, metadata: Dict) -> Dict:
        """
        Process content in maintenance mode (accept everything with logging).
        
        Args:
            channel: Channel number
            audio_file_path: Audio file path
            transcript: Transcript text
            confidence: STT confidence
            metadata: Original metadata
            
        Returns:
            Maintenance processing results
        """
        try:
            # Route to playable directory
            destination_paths = self._route_files(channel, audio_file_path, transcript, True, {})
            
            maintenance_metadata = {
                'channel': channel,
                'timestamp': datetime.now().isoformat(),
                'is_acceptable': True,
                'maintenance_mode': True,
                'transcript': transcript,
                'stt_confidence': confidence,
                'destination_paths': destination_paths,
                'original_metadata': metadata,
                'filter_results': {'maintenance_mode': True}
            }
            
            # Update stats
            with self.stats_lock:
                self.filter_stats['total_processed'] += 1
                self.filter_stats['total_accepted'] += 1
            
            self.logger.info(f"Maintenance mode: Channel {channel} content accepted for testing")
            return maintenance_metadata
            
        except Exception as e:
            self.logger.error(f"Maintenance mode processing failed: {e}")
            return self._create_error_result(channel, audio_file_path, str(e))
    
    def _create_error_result(self, channel: int, audio_file_path: str, error_message: str) -> Dict:
        """
        Create error result dictionary.
        
        Args:
            channel: Channel number
            audio_file_path: Audio file path
            error_message: Error message
            
        Returns:
            Error result dictionary
        """
        return {
            'channel': channel,
            'timestamp': datetime.now().isoformat(),
            'is_acceptable': False,
            'error': error_message,
            'original_file': audio_file_path,
            'filter_results': {'error': True, 'message': error_message}
        }
    
    # =============================================================================
    # REQUIRED INTERFACES FOR SYSTEM INTEGRATION
    # =============================================================================
    
    def process_transcript_file(self, file_path: str, channel: int) -> Dict:
        """
        Process transcript file for Queue Manager integration.
        
        Args:
            file_path: Path to transcript file
            channel: Channel number
            
        Returns:
            Processing results
        """
        try:
            if not os.path.exists(file_path):
                return self._create_error_result(channel, file_path, "Transcript file not found")
            
            # Read transcript
            with open(file_path, 'r', encoding='utf-8') as f:
                transcript = f.read().strip()
            
            # Look for corresponding audio file
            audio_file = None
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            
            # Search in recordings directory
            recordings_channel_dir = os.path.join(self.recordings_dir, f'channel_{channel}')
            if os.path.exists(recordings_channel_dir):
                for ext in ['.wav', '.mp3', '.flac']:
                    potential_audio = os.path.join(recordings_channel_dir, base_name + ext)
                    if os.path.exists(potential_audio):
                        audio_file = potential_audio
                        break
            
            if not audio_file:
                return self._create_error_result(channel, file_path, "Corresponding audio file not found")
            
            # Process with default confidence and metadata
            confidence = 0.7  # Default confidence for file-based processing
            metadata = {
                'processing_source': 'file',
                'transcript_file': file_path,
                'processing_timestamp': datetime.now().isoformat()
            }
            
            return self.process_transcript(channel, audio_file, transcript, confidence, metadata)
            
        except Exception as e:
            self.logger.error(f"Error processing transcript file {file_path}: {e}")
            return self._create_error_result(channel, file_path, str(e))
    
    def get_filtering_status(self, file_path: str) -> Dict:
        """
        Get filtering status for specific file.
        
        Args:
            file_path: File path to check
            
        Returns:
            Status dictionary
        """
        try:
            # Check if file exists in playable directories
            for channel in range(1, 6):
                playable_audio_dir = os.path.join(self.playable_dir, f'channel_{channel}', 'audio')
                if os.path.exists(playable_audio_dir):
                    for audio_file in os.listdir(playable_audio_dir):
                        if os.path.basename(file_path) in audio_file:
                            return {
                                'file_path': file_path,
                                'status': 'accepted',
                                'channel': channel,
                                'location': os.path.join(playable_audio_dir, audio_file)
                            }
                
                # Check in bin directories
                bin_audio_dir = os.path.join(self.bin_dir, f'channel_{channel}', 'audio')
                if os.path.exists(bin_audio_dir):
                    for audio_file in os.listdir(bin_audio_dir):
                        if os.path.basename(file_path) in audio_file:
                            return {
                                'file_path': file_path,
                                'status': 'filtered',
                                'channel': channel,
                                'location': os.path.join(bin_audio_dir, audio_file)
                            }
            
            return {
                'file_path': file_path,
                'status': 'not_found'
            }
            
        except Exception as e:
            self.logger.error(f"Error getting filtering status for {file_path}: {e}")
            return {
                'file_path': file_path,
                'status': 'error',
                'error': str(e)
            }
    
    def get_playable_files(self, channel: int) -> List[str]:
        """
        Get playable files for Audio Output Manager integration.
        
        Args:
            channel: Channel number (1-5)
            
        Returns:
            List of playable file paths
        """
        try:
            playable_files = []
            audio_dir = os.path.join(self.playable_dir, f'channel_{channel}', 'audio')
            
            if os.path.exists(audio_dir):
                for filename in sorted(os.listdir(audio_dir)):
                    if filename.endswith(('.wav', '.mp3', '.flac')):
                        full_path = os.path.join(audio_dir, filename)
                        playable_files.append(full_path)
            
            # Sort by modification time (newest first)
            playable_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return playable_files
            
        except Exception as e:
            self.logger.error(f"Error getting playable files for channel {channel}: {e}")
            return []
    
    def get_file_metadata(self, file_path: str) -> Optional[Dict]:
        """
        Get metadata for specific file.
        
        Args:
            file_path: File path
            
        Returns:
            Metadata dictionary or None
        """
        try:
            # Look for metadata file
            metadata_file = file_path.replace('.wav', '_metadata.json').replace('.mp3', '_metadata.json').replace('.flac', '_metadata.json')
            
            # If direct metadata file doesn't exist, search in appropriate metadata directory
            if not os.path.exists(metadata_file):
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                
                # Search in playable and bin metadata directories
                for base_dir in [self.playable_dir, self.bin_dir]:
                    for channel in range(1, 6):
                        metadata_dir = os.path.join(base_dir, f'channel_{channel}', 'metadata')
                        potential_metadata = os.path.join(metadata_dir, f'{base_name}_metadata.json')
                        if os.path.exists(potential_metadata):
                            metadata_file = potential_metadata
                            break
            
            if os.path.exists(metadata_file):
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting metadata for {file_path}: {e}")
            return None
    
    def start_content_filtering(self) -> bool:
        """
        Start content filtering for Main Controller integration.
        
        Returns:
            True if started successfully
        """
        try:
            # Reset emergency and maintenance modes
            self.emergency_bypass = False
            self.maintenance_mode = False
            
            # Reset statistics
            with self.stats_lock:
                self.filter_stats = {
                    'total_processed': 0,
                    'total_filtered': 0,
                    'total_accepted': 0,
                    'by_channel': {f'channel_{i}': {'processed': 0, 'filtered': 0, 'accepted': 0} for i in range(1, 6)},
                    'by_category': {cat.value: 0 for cat in FilterCategory},
                    'start_time': datetime.now()
                }
            
            self.logger.info("Content filtering started")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start content filtering: {e}")
            return False
    
    def stop_content_filtering(self) -> bool:
        """
        Stop content filtering for Main Controller integration.
        
        Returns:
            True if stopped successfully
        """
        try:
            self.logger.info("Content filtering stopped")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to stop content filtering: {e}")
            return False
    
    def get_filter_status(self) -> Dict:
        """
        Get content filter status for Main Controller integration.
        
        Returns:
            Status dictionary
        """
        try:
            with self.stats_lock:
                status = {
                    'timestamp': datetime.now().isoformat(),
                    'mode': self.filter_mode.value,
                    'emergency_bypass': self.emergency_bypass,
                    'maintenance_mode': self.maintenance_mode,
                    'statistics': self.filter_stats.copy(),
                    'configuration': {
                        'case_sensitive': self.case_sensitive,
                        'min_confidence_threshold': self.min_confidence_threshold,
                        'intelligibility_threshold': self.intelligibility_threshold,
                        'min_transcript_length': self.min_transcript_length,
                        'max_transcript_length': self.max_transcript_length
                    },
                    'filter_counts': {
                        'total_words': sum(len(words) for words in self.filtered_content.values()),
                        'total_phrases': sum(len(phrases) for phrases in self.filtered_phrases.values()),
                        'total_patterns': sum(len(patterns) for patterns in self.regex_patterns.values())
                    }
                }
            
            return status
            
        except Exception as e:
            self.logger.error(f"Error getting filter status: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'status': 'error'
            }
    
    def update_filter_config(self, new_config: Dict) -> bool:
        """
        Update filter configuration for Main Controller integration.
        
        Args:
            new_config: New configuration dictionary
            
        Returns:
            True if updated successfully
        """
        try:
            # Update filter configuration
            self.filter_config.update(new_config)
            
            # Reload filtered content if word lists changed
            if any(key in new_config for key in ['filtered_words', 'filtered_phrases', 'categories']):
                self._load_filtered_content()
            
            # Update mode if specified
            if 'mode' in new_config:
                self.filter_mode = FilterMode(new_config['mode'])
            
            # Update quality settings if specified
            quality_config = new_config.get('quality_assessment', {})
            if quality_config:
                self.quality_config.update(quality_config)
                self.min_transcript_length = self.quality_config.get('min_length', self.min_transcript_length)
                self.max_transcript_length = self.quality_config.get('max_length', self.max_transcript_length)
                self.min_confidence_threshold = self.quality_config.get('min_confidence', self.min_confidence_threshold)
                self.intelligibility_threshold = self.quality_config.get('intelligibility_threshold', self.intelligibility_threshold)
            
            self.logger.info("Filter configuration updated successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update filter configuration: {e}")
            return False
    
    def emergency_bypass(self, enable: bool = True) -> bool:
        """
        Enable/disable emergency bypass mode.
        
        Args:
            enable: Whether to enable bypass mode
            
        Returns:
            True if operation successful
        """
        try:
            self.emergency_bypass = enable
            
            if enable:
                self.logger.warning("Emergency bypass mode ENABLED - All content will be accepted")
            else:
                self.logger.info("Emergency bypass mode disabled")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to set emergency bypass: {e}")
            return False
    
    def set_maintenance_mode(self, enable: bool = True) -> bool:
        """
        Enable/disable maintenance mode.
        
        Args:
            enable: Whether to enable maintenance mode
            
        Returns:
            True if operation successful
        """
        try:
            self.maintenance_mode = enable
            
            if enable:
                self.logger.info("Maintenance mode enabled - All content accepted for testing")
            else:
                self.logger.info("Maintenance mode disabled")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to set maintenance mode: {e}")
            return False
    
    def get_filter_statistics(self) -> Dict:
        """
        Get comprehensive filtering statistics.
        
        Returns:
            Statistics dictionary
        """
        try:
            with self.stats_lock:
                stats = self.filter_stats.copy()
                
                # Add calculated fields
                total_processed = stats['total_processed']
                if total_processed > 0:
                    stats['acceptance_rate'] = stats['total_accepted'] / total_processed
                    stats['rejection_rate'] = stats['total_filtered'] / total_processed
                else:
                    stats['acceptance_rate'] = 0.0
                    stats['rejection_rate'] = 0.0
                
                # Add runtime
                runtime = datetime.now() - stats['start_time']
                stats['runtime_seconds'] = runtime.total_seconds()
                stats['runtime_formatted'] = str(runtime)
                
                # Add processing rate
                if runtime.total_seconds() > 0:
                    stats['processing_rate_per_hour'] = total_processed / (runtime.total_seconds() / 3600)
                else:
                    stats['processing_rate_per_hour'] = 0.0
                
                return stats
                
        except Exception as e:
            self.logger.error(f"Error getting filter statistics: {e}")
            return {}
    
    def cleanup(self):
        """Clean up content filter resources."""
        try:
            self.logger.info("Content filter cleanup completed")
        except Exception as e:
            self.logger.error(f"Content filter cleanup failed: {e}")