import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock pyaudio before importing the module that uses it
with patch.dict('sys.modules', {'pyaudio': unittest.mock.MagicMock()}):
    from processing.content_filter import ContentFilter

class TestContentFilter(unittest.TestCase):

    def test_filter_words(self):
        """Test that the content filter correctly identifies and filters words."""
        config = {
            'content_filter': {
                'filtered_words': ['badword', 'anotherone'],
                'filtered_phrases': []
            },
            'paths': {
                'bin': './bin',
                'playable': './playable'
            }
        }
        content_filter = ContentFilter(config)

        result = content_filter.process_transcript(1, 'test.wav', 'this is a badword', 0.9, {})
        self.assertFalse(result['is_acceptable'])

        result = content_filter.process_transcript(1, 'test.wav', 'this is a good word', 0.9, {})
        self.assertTrue(result['is_acceptable'])

    def test_filter_phrases(self):
        """Test that the content filter correctly identifies and filters phrases."""
        config = {
            'content_filter': {
                'filtered_words': [],
                'filtered_phrases': ['bad phrase', 'another one']
            },
            'paths': {
                'bin': './bin',
                'playable': './playable'
            }
        }
        content_filter = ContentFilter(config)

        result = content_filter.process_transcript(1, 'test.wav', 'this is a bad phrase', 0.9, {})
        self.assertFalse(result['is_acceptable'])

        result = content_filter.process_transcript(1, 'test.wav', 'this is a good phrase', 0.9, {})
        self.assertTrue(result['is_acceptable'])

if __name__ == '__main__':
    unittest.main()