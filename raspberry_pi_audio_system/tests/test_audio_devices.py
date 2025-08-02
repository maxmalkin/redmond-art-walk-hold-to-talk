import unittest
from unittest.mock import patch, MagicMock
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from hardware.audio_devices import AudioDeviceManager

class TestAudioDeviceManager(unittest.TestCase):

    @patch('hardware.audio_devices.pyaudio')
    @patch('hardware.audio_devices.usb.core')
    def test_hotplug_monitoring(self, mock_usb_core, mock_pyaudio):
        """Test that the AudioDeviceManager can detect USB device changes."""
        # Mock initial USB devices
        mock_usb_core.find.return_value = [MagicMock(idVendor=0x1234, idProduct=0x5678)]

        # Mock PyAudio
        mock_pyaudio.PyAudio.return_value.get_device_count.return_value = 1
        mock_pyaudio.PyAudio.return_value.get_device_info_by_index.return_value = {
            'name': 'USB Microphone',
            'maxInputChannels': 1,
            'maxOutputChannels': 0,
            'defaultSampleRate': 44100
        }

        config = {
            'usb_devices': {
                'hot_plug_monitoring': True,
                'device_health_interval': 0.1
            }
        }

        manager = AudioDeviceManager(config)
        time.sleep(0.2)

        # Mock a change in USB devices
        mock_usb_core.find.return_value = [
            MagicMock(idVendor=0x1234, idProduct=0x5678),
            MagicMock(idVendor=0x4321, idProduct=0x8765)
        ]

        time.sleep(0.2)

        # Check that a device refresh was triggered
        self.assertGreater(manager.performance_stats['device_changes'], 0)

        manager.cleanup()

if __name__ == '__main__':
    unittest.main()
