import unittest
from unittest.mock import patch, MagicMock
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from hardware.audio_devices import AudioDeviceManager

class TestAudioDeviceManager(unittest.TestCase):

    @patch('hardware.audio_devices.pyaudio')
    @patch('hardware.audio_devices.AudioDeviceManager._get_connected_usb_devices')
    def test_hotplug_monitoring(self, mock_get_devices, mock_pyaudio):
        """Test that the AudioDeviceManager can detect USB device changes."""
        # Mock initial USB devices
        mock_get_devices.return_value = {(0x1234, 0x5678)}

        # Mock PyAudio
        mock_pyaudio.PyAudio.return_value.get_device_count.return_value = 1
        mock_pyaudio.PyAudio.return_value.get_device_info_by_index.return_value = {
            'name': 'USB Microphone',
            'maxInputChannels': 1,
            'maxOutputChannels': 0,
            'defaultSampleRate': 44100
        }
        # Set USB_MONITORING_AVAILABLE to true for the test
        with patch('hardware.audio_devices.USB_MONITORING_AVAILABLE', True):
            config = {
                'usb_devices': {
                    'hot_plug_monitoring': True,
                    'device_health_interval': 0.1
                }
            }

            manager = AudioDeviceManager(config)
            # Allow monitoring thread to run
            time.sleep(0.15)

            # Mock a change in USB devices
            mock_get_devices.return_value = {(0x1234, 0x5678), (0x4321, 0x8765)}

            # Allow monitoring thread to run again
            time.sleep(0.15)

            # Check that a device refresh was triggered
            self.assertGreater(manager.performance_stats['device_changes'], 0)

            manager.cleanup()

if __name__ == '__main__':
    unittest.main()