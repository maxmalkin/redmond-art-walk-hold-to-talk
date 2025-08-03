import unittest
from unittest.mock import MagicMock, patch
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from task_queue.file_queue import FileProcessingQueue, TaskStatus

class TestFileProcessingQueue(unittest.TestCase):

    def test_submit_and_process_task(self):
        """Test that a task can be submitted to the queue and processed."""
        mock_speech_processor = MagicMock()
        mock_content_filter = MagicMock()

        config = {
            'queue': {
                'max_workers': 1
            }
        }

        queue = FileProcessingQueue(mock_speech_processor, mock_content_filter, config)

        # Mock the processing functions
        mock_speech_processor.process_audio_file.return_value = {
            'channel': 1,
            'audio_file': 'test.wav',
            'transcript': 'test transcript',
            'confidence': 0.9,
            'metadata': {}
        }
        mock_content_filter.process_transcript.return_value = {'is_acceptable': True}

        task_id = queue.submit_task(1, 'test.wav', {})
        time.sleep(0.1)  # Allow time for processing

        status = queue.get_task_status(task_id)
        self.assertEqual(status['status'], TaskStatus.COMPLETED.value)

        queue.cleanup()

if __name__ == '__main__':
    unittest.main()
