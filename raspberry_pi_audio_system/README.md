# Redmond Art Walk - Hold to Talk Exhibit

A comprehensive audio recording and processing system for Raspberry Pi that handles 5 independent recording/playback channels with real-time speech-to-text processing and content filtering.

## System Overview

This system provides:

- **5 GPIO recording buttons** (hold-to-record functionality)
- **5 GPIO playback buttons** (momentary press to play)
- **USB microphone input** for audio recording
- **5 USB audio outputs** for independent playback channels
- **Real-time speech-to-text** processing using spchcat library (MANDATORY)
- **Content filtering** with configurable word/phrase filtering
- **Queue-based processing** for efficient audio handling
- **Channel mapping preservation** throughout the entire pipeline

## Hardware Requirements

### Audio Devices

- 1 USB microphone (MIC)
- 5 USB audio output devices (AUDIO_OUT_ONE through AUDIO_OUT_FIVE)

### GPIO Buttons

- 10 GPIO buttons total:
  - 5 recording buttons (REC_BUTTON_ONE through REC_BUTTON_FIVE)
  - 5 playback buttons (PHONE_UP_ONE through PHONE_UP_FIVE)

### System Requirements

- Raspberry Pi (3B+ or newer recommended)
- MicroSD card (32GB+ recommended)
- Raspberry Pi OS (Bullseye or newer)
- Internet connection for initial setup

## Channel Mapping

The system maintains strict channel mapping throughout the pipeline:

```
Channel 1: REC_BUTTON_ONE → MIC → STT → Filter → AUDIO_OUT_ONE → PHONE_UP_ONE
Channel 2: REC_BUTTON_TWO → MIC → STT → Filter → AUDIO_OUT_TWO → PHONE_UP_TWO
Channel 3: REC_BUTTON_THREE → MIC → STT → Filter → AUDIO_OUT_THREE → PHONE_UP_THREE
Channel 4: REC_BUTTON_FOUR → MIC → STT → Filter → AUDIO_OUT_FOUR → PHONE_UP_FOUR
Channel 5: REC_BUTTON_FIVE → MIC → STT → Filter → AUDIO_OUT_FIVE → PHONE_UP_FIVE
```

## System Logic

### Recording Process

1. **Hold REC_BUTTON_X** → Start recording from USB microphone
2. **Release REC_BUTTON_X** → Stop recording, queue for processing
3. **spchcat processes audio** → Speech-to-text conversion
4. **Content filter evaluates transcript** → Determines clean vs filtered
5. **File placement** → Moves to `playable/channel_X` or `bin/channel_X`

### Playback Process

1. **Press PHONE_UP_X** → Trigger playback on channel X
2. **System retrieves** → Latest clean audio file from `playable/channel_X`
3. **Audio plays** → Through corresponding AUDIO_OUT_X device

## Installation

### Automated Installation

1. **Clone or download the system:**

   ```bash
   cd /home/pi
   # Copy the raspberry_pi_audio_system directory to your Pi
   ```

2. **Run the installation script:**

   ```bash
   cd raspberry_pi_audio_system
   chmod +x install/setup.sh
   ./install/setup.sh
   ```

3. **Follow the installation prompts** - the script will:
   - Update system packages
   - Install required dependencies
   - Create Python virtual environment
   - Install spchcat (MANDATORY speech-to-text engine)
   - Configure GPIO and audio permissions
   - Set up system directories
   - Optionally install as systemd service

### Manual Installation

If you prefer manual installation:

1. **Install system dependencies:**

   ```bash
   sudo apt-get update
   sudo apt-get install -y python3 python3-pip python3-venv python3-dev
   sudo apt-get install -y libasound2-dev portaudio19-dev libportaudio2
   sudo apt-get install -y build-essential cmake git pkg-config
   sudo apt-get install -y libssl-dev libcurl4-openssl-dev libjson-c-dev
   ```

2. **Create virtual environment:**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r install/requirements.txt
   ```

3. **Install spchcat (MANDATORY):**

   ```bash
   chmod +x install/spchcat_setup.sh
   ./install/spchcat_setup.sh
   ```

4. **Setup permissions:**
   ```bash
   sudo usermod -a -G gpio,audio $USER
   ```

## Configuration

### Main Configuration File

Edit `config.yaml` to customize your system:

```yaml
# GPIO pin assignments
gpio:
  recording_buttons:
    REC_BUTTON_ONE: 2
    REC_BUTTON_TWO: 3
    # ... etc
  playback_buttons:
    PHONE_UP_ONE: 22
    PHONE_UP_TWO: 10
    # ... etc

# Audio settings
audio:
  sample_rate: 44100
  chunk_size: 1024
  format: "paInt16"

# spchcat configuration (MANDATORY)
spchcat:
  binary_path: "/usr/local/bin/spchcat"
  model_path: "/usr/local/share/spchcat/models"
  language: "en"
  confidence_threshold: 0.7

# Content filtering
content_filter:
  strict_mode: true
  filtered_words:
    - "inappropriate"
    - "profanity"
  filtered_phrases:
    - "inappropriate phrase"
```

### Environment Variables

You can override configuration with environment variables:

```bash
export AUDIO_SAMPLE_RATE=48000
export SPCHCAT_LANGUAGE=en
export LOG_LEVEL=DEBUG
```

## Usage

### Starting the System

#### Manual Start

```bash
cd raspberry_pi_audio_system
source venv/bin/activate
python main.py
```

#### With Systemd Service

```bash
sudo systemctl start raspberry-pi-audio
sudo systemctl status raspberry-pi-audio
```

#### Test Mode

```bash
python main.py --test-mode
```

### System Operation

1. **Recording:**

   - Hold any REC_BUTTON (1-5) to start recording
   - Release to stop and process the recording
   - Audio is processed through spchcat for speech-to-text
   - Content is filtered and placed in appropriate folder

2. **Playback:**

   - Press any PHONE_UP button (1-5) to play the latest clean recording from that channel
   - Audio plays through the corresponding USB audio output

3. **Monitoring:**
   - Check logs in `logs/system.log`
   - Monitor file placement in `playable/channel_X` and `bin/channel_X` directories

### Command Line Options

```bash
python main.py --help
python main.py --config custom_config.yaml
python main.py --verbose
python main.py --test-mode
```

## Directory Structure

```
raspberry_pi_audio_system/
├── main.py                 # Main system controller
├── config.yaml            # System configuration
├── hardware/              # Hardware interface modules
│   ├── gpio_handler.py     # GPIO button management
│   └── audio_devices.py    # USB audio device management
├── processing/             # Audio processing modules
│   ├── recorder.py         # Audio recording
│   ├── speech_processor.py # spchcat integration
│   └── content_filter.py   # Content filtering
├── queue/                  # Queue management
│   └── file_queue.py       # Processing queue
├── playback/               # Audio playback
│   └── output_manager.py   # Multi-channel output
├── utils/                  # Utility modules
│   ├── file_manager.py     # File organization
│   └── config.py           # Configuration management
├── install/                # Installation scripts
│   ├── setup.sh           # Main installation script
│   ├── spchcat_setup.sh   # spchcat installation
│   └── requirements.txt   # Python dependencies
├── recordings/             # Temporary recordings
├── temp/                   # Temporary processing files
├── bin/                    # Filtered audio files (by channel)
├── playable/               # Clean audio files (by channel)
├── logs/                   # System logs
└── backup/                 # Backup storage
```

## File Organization

### Channel-Based Organization

- `playable/channel_1/` - Clean audio files for channel 1
- `playable/channel_2/` - Clean audio files for channel 2
- `bin/channel_1/` - Filtered audio files for channel 1
- `bin/channel_2/` - Filtered audio files for channel 2
- etc.

### Processing Flow

1. **Recording** → `temp/` (during recording)
2. **Processing** → spchcat analysis + content filtering
3. **Clean Audio** → `playable/channel_X/`
4. **Filtered Audio** → `bin/channel_X/`

## Speech Processing

### spchcat Integration (MANDATORY)

This system uses spchcat as the ONLY approved speech-to-text engine. Other STT solutions are not supported.

**Key Features:**

- Real-time speech recognition
- Configurable confidence thresholds
- Multiple language support
- Optimized for Raspberry Pi

**Configuration:**

```yaml
spchcat:
  binary_path: "/usr/local/bin/spchcat"
  model_path: "/usr/local/share/spchcat/models"
  language: "en"
  timeout: 30
  confidence_threshold: 0.7
```

## Content Filtering

### Filter Configuration

The content filter evaluates speech transcripts against configured word and phrase lists:

```yaml
content_filter:
  strict_mode: true # Any match = filtered
  case_sensitive: false # Case insensitive matching
  filtered_words: # Individual words to filter
    - "inappropriate"
    - "profanity"
  filtered_phrases: # Phrases to filter
    - "inappropriate phrase"
```

### Filter Behavior

- **Clean Audio** → Goes to `playable/channel_X/` and can be played
- **Filtered Audio** → Goes to `bin/channel_X/` and cannot be played
- **Strict Mode** → Any filtered word/phrase = entire recording filtered
- **Non-Strict Mode** → Confidence-based filtering (future enhancement)

## Monitoring and Maintenance

### Logging

Logs are written to `logs/system.log` with configurable levels:

- DEBUG: Detailed debugging information
- INFO: General system information
- WARNING: Warning conditions
- ERROR: Error conditions

### Automatic Maintenance

The system performs automatic maintenance:

- **Temporary File Cleanup** - Removes old temp files
- **Log Rotation** - Manages log file sizes
- **File Limits** - Maintains max files per channel
- **Queue Management** - Clears completed processing tasks

### Manual Maintenance

```bash
# View system logs
tail -f logs/system.log

# Check disk usage
du -sh playable/ bin/ logs/

# Manual cleanup
find temp/ -mtime +1 -delete
```

## Troubleshooting

### Common Issues

#### spchcat Not Found

```bash
# Verify spchcat installation
which spchcat
spchcat --version

# Reinstall if needed
./install/spchcat_setup.sh
```

#### Audio Device Issues

```bash
# List audio devices
aplay -l
arecord -l

# Test USB audio
speaker-test -c 1 -t sine
```

#### GPIO Permission Issues

```bash
# Check GPIO group membership
groups $USER

# Add to GPIO group if missing
sudo usermod -a -G gpio $USER
# Logout and login again
```

#### No Audio Recording

- Check USB microphone connection
- Verify audio device permissions
- Check ALSA/PulseAudio configuration
- Test with: `arecord -D hw:1 test.wav`

#### No Audio Playback

- Verify USB audio output devices
- Check channel mapping in config.yaml
- Test each output device individually
- Verify audio file format compatibility

### Debug Mode

Enable verbose logging:

```bash
python main.py --verbose
```

Or set in config:

```yaml
logging:
  level: "DEBUG"
```

### System Status

Check system status:

```bash
# If running as service
sudo systemctl status raspberry-pi-audio

# Check process
ps aux | grep main.py

# Check ports/resources
lsof -p $(pgrep -f main.py)
```

## Development

### Adding Custom Components

The system is designed for easy extension:

1. **Create new module** in appropriate directory
2. **Implement standard interface** (initialize, cleanup, etc.)
3. **Add to main.py** initialization sequence
4. **Update configuration** as needed

### Testing

```bash
# Test system initialization only
python main.py --test-mode

# Test individual components
python -c "from hardware.gpio_handler import GPIOHandler; print('GPIO OK')"
python -c "from processing.speech_processor import SpeechProcessor; print('spchcat OK')"
```

## Performance Optimization

### Raspberry Pi Optimization

- **Use Class 10 MicroSD** for better I/O performance
- **Enable GPU memory split** for audio processing
- **Disable unnecessary services** to free resources
- **Use USB 3.0 hub** if available for audio devices

### Configuration Tuning

```yaml
# Optimize for performance
audio:
  chunk_size: 2048 # Larger chunks = less CPU overhead
queue:
  max_workers: 1 # Fewer workers on slower Pi models
spchcat:
  confidence_threshold: 0.8 # Higher threshold = faster processing
```

## Security Considerations

- **File Permissions** - Restricted access to audio files
- **Content Filtering** - Prevents inappropriate content storage
- **Log Management** - Automatic cleanup prevents disk filling
- **Service User** - Run as non-root user for security

## Support and Contributions

### Getting Help

1. Check this README for common solutions
2. Review logs in `logs/system.log`
3. Test in `--test-mode` to isolate issues
4. Verify hardware connections and permissions

### System Requirements Verification

Run the verification script:

```bash
python main.py --test-mode --verbose
```

This will test all components without requiring physical hardware.

## License

This project is designed for the Raspberry Pi Audio Recording & Processing System. All components are integrated to work together as a complete solution.

## Version Information

- **Version:** 1.0.0
- **spchcat Requirement:** MANDATORY - No alternative STT engines supported
- **Raspberry Pi OS:** Bullseye or newer
- **Python:** 3.7+

---

**IMPORTANT NOTES:**

1. **spchcat is MANDATORY** - This is the only approved speech-to-text engine for this system
2. **Channel mapping must be preserved** - Recording button X always maps to output channel X
3. **USB audio devices required** - Built-in Pi audio is not supported for multi-channel operation
4. **Real-time processing** - System is designed for immediate audio processing, not batch processing

For technical support and system integration, refer to the component documentation in each module's docstrings.
