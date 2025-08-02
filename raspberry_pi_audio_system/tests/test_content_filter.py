import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from processing.content_filter import ContentFilter

class TestContentFilter(unittest.TestCase):

    def test_filter_words(self):
        """Test that the content filter correctly identifies and filters words."""
        config = {
            'content_filter': {
                'filtered_words': ['badword', 'anotherone'],
                'filtered_phrases': []
            }
        }
        content_filter = ContentFilter(config)

        self.assertTrue(content_filter.is_inappropriate('this is a badword'))
        self.assertFalse(content_filter.is_inappropriate('this is a good word'))

    def test_filter_phrases(self):
        """Test that the content filter correctly identifies and filters phrases."""
        config = {
            'content_filter': {
                'filtered_words': [],
                'filtered_phrases': ['bad phrase', 'another one']
            }
        }
        content_filter = ContentFilter(config)

        self.assertTrue(content_filter.is_inappropriate('this is a bad phrase'))
        self.assertFalse(content_filter.is_inappropriate('this is a good phrase'))

if __name__ == '__main__':
    unittest.main()
