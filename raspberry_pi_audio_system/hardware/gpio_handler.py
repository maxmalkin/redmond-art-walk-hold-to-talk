"""
GPIO Handler for Raspberry Pi Audio System.

Manages 10 GPIO buttons:
- 5 recording buttons (REC_BUTTON_ONE through REC_BUTTON_FIVE) - hold-to-record
- 5 playback buttons (PHONE_UP_ONE through PHONE_UP_FIVE) - momentary press

Each recording button maps to its corresponding audio output channel:
REC_BUTTON_ONE → AUDIO_OUT_ONE → PHONE_UP_ONE
"""

import threading
import time
import logging
from typing import Dict, Callable, Optional, List
from enum import Enum
from collections import deque
import os

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    from gpiozero import Button, Device
    from gpiozero.pins.pigpio import PiGPIOFactory
    # Use pigpio for better performance if available
    try:
        Device.pin_factory = PiGPIOFactory()
    except:
        pass  # Fall back to default pin factory
    RASPBERRY_PI = True
except ImportError:
    # Mock GPIO for development on non-Pi systems
    class MockGPIO:
        BCM = "BCM"
        IN = "IN"
        PUD_UP = "PUD_UP"
        FALLING = "FALLING"
        RISING = "RISING"
        
        @staticmethod
        def setmode(mode): pass
        @staticmethod
        def setup(pin, mode, pull_up_down=None): pass
        @staticmethod
        def input(pin): return True
        @staticmethod
        def add_event_detect(pin, edge, callback=None, bouncetime=None): pass
        @staticmethod
        def cleanup(): pass
    
    class MockButton:
        def __init__(self, pin, pull_up=True, bounce_time=None, hold_time=1, hold_repeat=False):
            self.pin = pin
            self.is_pressed = False
            self.when_pressed = None
            self.when_released = None
            self.when_held = None
            
        def close(self): pass
    
    GPIO = MockGPIO()
    Button = MockButton
    RASPBERRY_PI = False


class ButtonType(Enum):
    """Button type enumeration."""
    RECORDING = "recording"
    PLAYBACK = "playback"


class ButtonState(Enum):
    """Button state enumeration."""
    PRESSED = "pressed"
    RELEASED = "released"


class GPIOHandler:
    """
    GPIO button handler for audio recording and playback system.
    
    Manages button events and maintains button-channel mapping throughout
    the system lifecycle. Optimized for Raspberry Pi hardware.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize GPIO handler with configuration.
        
        Args:
            config: Configuration dictionary containing GPIO pin mappings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Button pin mappings from config
        self.recording_pins = config.get('gpio', {}).get('recording_buttons', {})
        self.playback_pins = config.get('gpio', {}).get('playback_buttons', {})
        
        # GPIO configuration
        self.gpio_config = config.get('gpio', {})
        self.debounce_time = self.gpio_config.get('debounce_time', 50)  # ms
        self.hold_time = self.gpio_config.get('hold_time', 100)  # ms
        self.poll_interval = self.gpio_config.get('poll_interval', 0.01)  # 10ms
        
        # Callback mappings
        self.recording_callbacks: Dict[int, Callable] = {}
        self.playback_callbacks: Dict[int, Callable] = {}
        
        # Button state tracking
        self.button_states: Dict[int, ButtonState] = {}
        self.recording_active: Dict[int, bool] = {}
        self.last_button_times: Dict[int, float] = {}
        
        # Event queues for thread-safe operation
        self.recording_events = deque(maxlen=100)
        self.playback_events = deque(maxlen=100)
        self.event_lock = threading.Lock()
        
        # Threading for monitoring
        self.monitoring_thread: Optional[threading.Thread] = None
        self.event_processing_thread: Optional[threading.Thread] = None
        self.stop_monitoring = threading.Event()
        
        # Button objects for gpiozero (if on Pi)
        self.recording_buttons: Dict[int, Button] = {}
        self.playback_buttons: Dict[int, Button] = {}
        
        # Performance monitoring
        self.button_press_counts: Dict[int, int] = {}
        self.performance_stats = {
            'total_presses': 0,
            'missed_events': 0,
            'processing_times': deque(maxlen=1000)
        }
        
        # Initialize GPIO
        self._setup_gpio()
    
    def _setup_gpio(self):
        """Initialize GPIO pins and setup event detection."""
        try:
            if RASPBERRY_PI:
                # Use gpiozero for better hardware integration
                self._setup_gpiozero_buttons()
            else:
                # Fallback to RPi.GPIO for compatibility
                self._setup_rpi_gpio()
            
            self.logger.info("GPIO setup completed successfully")
            
        except Exception as e:
            self.logger.error(f"GPIO setup failed: {e}")
            raise
    
    def _setup_gpiozero_buttons(self):
        """Setup buttons using gpiozero library for optimal Pi performance."""
        # Setup recording buttons (hold-to-record)
        for button_name, pin in self.recording_pins.items():
            try:
                button = Button(
                    pin, 
                    pull_up=True, 
                    bounce_time=self.debounce_time / 1000.0,  # Convert to seconds
                    hold_time=self.hold_time / 1000.0,
                    hold_repeat=False
                )
                
                # Setup callbacks
                channel = self._pin_to_channel(pin, ButtonType.RECORDING)
                button.when_pressed = lambda ch=channel: self._on_recording_button_pressed(ch)
                button.when_released = lambda ch=channel: self._on_recording_button_released(ch)
                
                self.recording_buttons[pin] = button
                self.button_states[pin] = ButtonState.RELEASED
                self.recording_active[pin] = False
                self.button_press_counts[pin] = 0
                self.last_button_times[pin] = 0
                
                self.logger.info(f"Setup recording button {button_name} on pin {pin}")
                
            except Exception as e:
                self.logger.error(f"Failed to setup recording button {button_name}: {e}")
                raise
        
        # Setup playback buttons (momentary press)
        for button_name, pin in self.playback_pins.items():
            try:
                button = Button(
                    pin, 
                    pull_up=True, 
                    bounce_time=self.debounce_time / 1000.0,
                    hold_time=1.0,  # Longer hold time for playback
                    hold_repeat=False
                )
                
                # Setup callback
                channel = self._pin_to_channel(pin, ButtonType.PLAYBACK)
                button.when_pressed = lambda ch=channel: self._on_playback_button_pressed(ch)
                
                self.playback_buttons[pin] = button
                self.button_states[pin] = ButtonState.RELEASED
                self.button_press_counts[pin] = 0
                self.last_button_times[pin] = 0
                
                self.logger.info(f"Setup playback button {button_name} on pin {pin}")
                
            except Exception as e:
                self.logger.error(f"Failed to setup playback button {button_name}: {e}")
                raise
    
    def _setup_rpi_gpio(self):
        """Fallback GPIO setup using RPi.GPIO."""
        GPIO.setmode(GPIO.BCM)
        
        # Setup recording button pins
        for button_name, pin in self.recording_pins.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self.button_states[pin] = ButtonState.RELEASED
            self.recording_active[pin] = False
            self.button_press_counts[pin] = 0
            self.last_button_times[pin] = 0
            self.logger.info(f"Setup recording button {button_name} on pin {pin}")
        
        # Setup playback button pins
        for button_name, pin in self.playback_pins.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self.button_states[pin] = ButtonState.RELEASED
            self.button_press_counts[pin] = 0
            self.last_button_times[pin] = 0
            self.logger.info(f"Setup playback button {button_name} on pin {pin}")
    
    def _on_recording_button_pressed(self, channel: int):
        """Handle recording button press via gpiozero callback."""
        with self.event_lock:
            self.recording_events.append(('pressed', channel, time.time()))
    
    def _on_recording_button_released(self, channel: int):
        """Handle recording button release via gpiozero callback."""
        with self.event_lock:
            self.recording_events.append(('released', channel, time.time()))
    
    def _on_playback_button_pressed(self, channel: int):
        """Handle playback button press via gpiozero callback."""
        with self.event_lock:
            self.playback_events.append(('pressed', channel, time.time()))
    
    def register_recording_callback(self, button_channel: int, callback: Callable):
        """
        Register callback for recording button events.
        
        Args:
            button_channel: Channel number (1-5)
            callback: Function to call on button events (start_recording, stop_recording)
        """
        button_name = f"REC_BUTTON_{self._channel_to_name(button_channel)}"
        pin = self.recording_pins.get(button_name)
        
        if pin is None:
            raise ValueError(f"Invalid recording button channel: {button_channel}")
        
        self.recording_callbacks[pin] = callback
        self.logger.info(f"Registered recording callback for channel {button_channel} (pin {pin})")
    
    def register_playback_callback(self, button_channel: int, callback: Callable):
        """
        Register callback for playback button events.
        
        Args:
            button_channel: Channel number (1-5)
            callback: Function to call on button press
        """
        button_name = f"PHONE_UP_{self._channel_to_name(button_channel)}"
        pin = self.playback_pins.get(button_name)
        
        if pin is None:
            raise ValueError(f"Invalid playback button channel: {button_channel}")
        
        self.playback_callbacks[pin] = callback
        self.logger.info(f"Registered playback callback for channel {button_channel} (pin {pin})")
    
    def start_monitoring(self):
        """Start monitoring GPIO buttons in separate threads."""
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.logger.warning("GPIO monitoring already running")
            return
        
        self.stop_monitoring.clear()
        
        if RASPBERRY_PI:
            # Start event processing thread for gpiozero events
            self.event_processing_thread = threading.Thread(
                target=self._process_events, daemon=True
            )
            self.event_processing_thread.start()
        else:
            # Start polling thread for RPi.GPIO fallback
            self.monitoring_thread = threading.Thread(
                target=self._monitor_buttons, daemon=True
            )
            self.monitoring_thread.start()
        
        # Start performance monitoring
        self.monitoring_thread = threading.Thread(
            target=self._monitor_performance, daemon=True
        )
        self.monitoring_thread.start()
        
        self.logger.info("Started GPIO button monitoring")
    
    def _process_events(self):
        """Process button events from queues (for gpiozero)."""
        while not self.stop_monitoring.is_set():
            try:
                # Process recording events
                with self.event_lock:
                    while self.recording_events:
                        event_type, channel, timestamp = self.recording_events.popleft()
                        self._handle_recording_event(channel, event_type, timestamp)
                
                # Process playback events  
                with self.event_lock:
                    while self.playback_events:
                        event_type, channel, timestamp = self.playback_events.popleft()
                        self._handle_playback_event(channel, timestamp)
                
                time.sleep(0.001)  # 1ms event processing loop
                
            except Exception as e:
                self.logger.error(f"Error processing button events: {e}")
                time.sleep(0.1)
    
    def _handle_recording_event(self, channel: int, event_type: str, timestamp: float):
        """Handle recording button events with improved timing."""
        try:
            start_time = time.time()
            
            if event_type == 'pressed' and not self.recording_active.get(channel, False):
                self.recording_active[channel] = True
                callback = self._get_recording_callback(channel)
                if callback:
                    callback(channel, "start_recording")
                    self.logger.info(f"Started recording on channel {channel}")
                    
            elif event_type == 'released' and self.recording_active.get(channel, False):
                self.recording_active[channel] = False
                callback = self._get_recording_callback(channel)
                if callback:
                    callback(channel, "stop_recording")
                    self.logger.info(f"Stopped recording on channel {channel}")
            
            # Track performance
            processing_time = time.time() - start_time
            self.performance_stats['processing_times'].append(processing_time)
            self.performance_stats['total_presses'] += 1
            
        except Exception as e:
            self.logger.error(f"Error handling recording event: {e}")
            self.performance_stats['missed_events'] += 1
    
    def _handle_playback_event(self, channel: int, timestamp: float):
        """Handle playback button events with debouncing."""
        try:
            start_time = time.time()
            
            # Debounce check
            last_time = self.last_button_times.get(channel, 0)
            if timestamp - last_time < (self.debounce_time / 1000.0):
                return
            
            self.last_button_times[channel] = timestamp
            callback = self._get_playback_callback(channel)
            if callback:
                callback(channel)
                self.logger.info(f"Playback triggered on channel {channel}")
            
            # Track performance
            processing_time = time.time() - start_time
            self.performance_stats['processing_times'].append(processing_time)
            self.performance_stats['total_presses'] += 1
            
        except Exception as e:
            self.logger.error(f"Error handling playback event: {e}")
            self.performance_stats['missed_events'] += 1
    
    def _get_recording_callback(self, channel: int) -> Optional[Callable]:
        """Get recording callback for channel."""
        button_name = f"REC_BUTTON_{self._channel_to_name(channel)}"
        pin = self.recording_pins.get(button_name)
        return self.recording_callbacks.get(pin) if pin else None
    
    def _get_playback_callback(self, channel: int) -> Optional[Callable]:
        """Get playback callback for channel."""
        button_name = f"PHONE_UP_{self._channel_to_name(channel)}"
        pin = self.playback_pins.get(button_name)
        return self.playback_callbacks.get(pin) if pin else None
    
    def _monitor_performance(self):
        """Monitor GPIO performance and system resources."""
        while not self.stop_monitoring.is_set():
            try:
                # Log performance stats every 60 seconds
                if self.performance_stats['total_presses'] > 0:
                    avg_processing_time = sum(self.performance_stats['processing_times']) / len(self.performance_stats['processing_times'])
                    self.logger.debug(
                        f"GPIO Performance - Total: {self.performance_stats['total_presses']}, "
                        f"Missed: {self.performance_stats['missed_events']}, "
                        f"Avg Processing: {avg_processing_time:.4f}s"
                    )
                
                # Monitor system resources if on Pi and psutil available
                if RASPBERRY_PI and PSUTIL_AVAILABLE:
                    cpu_percent = psutil.cpu_percent()
                    memory = psutil.virtual_memory()
                    if cpu_percent > 80 or memory.percent > 90:
                        self.logger.warning(
                            f"High system usage - CPU: {cpu_percent}%, Memory: {memory.percent}%"
                        )
                
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                self.logger.error(f"Error in performance monitoring: {e}")
                time.sleep(60)
    
    def stop_monitoring_buttons(self):
        """Stop monitoring GPIO buttons."""
        self.stop_monitoring.set()
        
        # Stop all monitoring threads
        threads_to_stop = [
            self.monitoring_thread,
            self.event_processing_thread
        ]
        
        for thread in threads_to_stop:
            if thread and thread.is_alive():
                thread.join(timeout=2.0)
                if thread.is_alive():
                    self.logger.warning(f"Thread {thread.name} did not stop gracefully")
        
        self.logger.info("Stopped GPIO button monitoring")
    
    def _monitor_buttons(self):
        """Monitor button states in polling loop."""
        while not self.stop_monitoring.is_set():
            try:
                # Monitor recording buttons (hold-to-record)
                for pin in self.recording_callbacks.keys():
                    current_state = ButtonState.PRESSED if not GPIO.input(pin) else ButtonState.RELEASED
                    previous_state = self.button_states.get(pin, ButtonState.RELEASED)
                    
                    if current_state != previous_state:
                        self.button_states[pin] = current_state
                        self._handle_recording_button_event(pin, current_state)
                
                # Monitor playback buttons (momentary press)
                for pin in self.playback_callbacks.keys():
                    current_state = ButtonState.PRESSED if not GPIO.input(pin) else ButtonState.RELEASED
                    previous_state = self.button_states.get(pin, ButtonState.RELEASED)
                    
                    if current_state == ButtonState.PRESSED and previous_state == ButtonState.RELEASED:
                        self.button_states[pin] = current_state
                        self._handle_playback_button_event(pin)
                    elif current_state == ButtonState.RELEASED and previous_state == ButtonState.PRESSED:
                        self.button_states[pin] = current_state
                
                time.sleep(0.01)  # 10ms polling interval
                
            except Exception as e:
                self.logger.error(f"Error in button monitoring: {e}")
                time.sleep(0.1)
    
    def _handle_recording_button_event(self, pin: int, state: ButtonState):
        """Handle recording button press/release events."""
        try:
            callback = self.recording_callbacks.get(pin)
            if not callback:
                return
            
            channel = self._pin_to_channel(pin, ButtonType.RECORDING)
            
            if state == ButtonState.PRESSED and not self.recording_active.get(pin, False):
                # Start recording
                self.recording_active[pin] = True
                callback(channel, "start_recording")
                self.logger.info(f"Started recording on channel {channel}")
                
            elif state == ButtonState.RELEASED and self.recording_active.get(pin, False):
                # Stop recording
                self.recording_active[pin] = False
                callback(channel, "stop_recording")
                self.logger.info(f"Stopped recording on channel {channel}")
                
        except Exception as e:
            self.logger.error(f"Error handling recording button event: {e}")
    
    def _handle_playback_button_event(self, pin: int):
        """Handle playback button press events."""
        try:
            callback = self.playback_callbacks.get(pin)
            if not callback:
                return
            
            channel = self._pin_to_channel(pin, ButtonType.PLAYBACK)
            callback(channel)
            self.logger.info(f"Playback triggered on channel {channel}")
            
        except Exception as e:
            self.logger.error(f"Error handling playback button event: {e}")
    
    def _pin_to_channel(self, pin: int, button_type: ButtonType) -> int:
        """Convert GPIO pin to channel number."""
        pin_mapping = self.recording_pins if button_type == ButtonType.RECORDING else self.playback_pins
        
        for button_name, button_pin in pin_mapping.items():
            if button_pin == pin:
                # Extract channel number from button name (e.g., "REC_BUTTON_ONE" -> 1)
                name_parts = button_name.split('_')
                channel_name = name_parts[-1]
                return self._name_to_channel(channel_name)
        
        raise ValueError(f"Pin {pin} not found in {button_type.value} mappings")
    
    def _channel_to_name(self, channel: int) -> str:
        """Convert channel number to name (1->ONE, 2->TWO, etc.)."""
        names = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE"}
        return names.get(channel, "UNKNOWN")
    
    def _name_to_channel(self, name: str) -> int:
        """Convert name to channel number (ONE->1, TWO->2, etc.)."""
        channels = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
        return channels.get(name, 0)
    
    def get_channel_mapping(self) -> Dict[int, Dict[str, int]]:
        """
        Get complete channel mapping for system integration.
        
        Returns:
            Dictionary mapping channels to their GPIO pins
        """
        mapping = {}
        
        for channel in range(1, 6):
            recording_pin = self.recording_pins.get(f"REC_BUTTON_{self._channel_to_name(channel)}")
            playback_pin = self.playback_pins.get(f"PHONE_UP_{self._channel_to_name(channel)}")
            
            mapping[channel] = {
                "recording_pin": recording_pin,
                "playback_pin": playback_pin
            }
        
        return mapping
    
    def get_button_state(self, button_id: str) -> Optional[ButtonState]:
        """Get current state of a specific button."""
        try:
            # Parse button ID to get pin
            if button_id.startswith('REC_BUTTON_'):
                pin = self.recording_pins.get(button_id)
            elif button_id.startswith('PHONE_UP_'):
                pin = self.playback_pins.get(button_id)
            else:
                return None
            
            return self.button_states.get(pin) if pin else None
            
        except Exception as e:
            self.logger.error(f"Error getting button state for {button_id}: {e}")
            return None
    
    def get_performance_stats(self) -> Dict:
        """Get GPIO performance statistics."""
        stats = self.performance_stats.copy()
        if self.performance_stats['processing_times']:
            stats['avg_processing_time'] = sum(self.performance_stats['processing_times']) / len(self.performance_stats['processing_times'])
            stats['max_processing_time'] = max(self.performance_stats['processing_times'])
        else:
            stats['avg_processing_time'] = 0
            stats['max_processing_time'] = 0
        
        stats['button_press_counts'] = self.button_press_counts.copy()
        return stats
    
    def get_system_health(self) -> Dict:
        """Get system health information."""
        health = {
            'gpio_initialized': bool(self.recording_pins and self.playback_pins),
            'monitoring_active': not self.stop_monitoring.is_set(),
            'recording_channels_active': sum(self.recording_active.values()),
            'total_buttons': len(self.recording_pins) + len(self.playback_pins)
        }
        
        if RASPBERRY_PI and PSUTIL_AVAILABLE:
            try:
                health['cpu_percent'] = psutil.cpu_percent()
                health['memory_percent'] = psutil.virtual_memory().percent
                health['temperature'] = self._get_cpu_temperature()
            except Exception as e:
                self.logger.warning(f"Could not get system health metrics: {e}")
        
        return health
    
    def _get_cpu_temperature(self) -> Optional[float]:
        """Get CPU temperature on Raspberry Pi."""
        try:
            if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp = int(f.read().strip()) / 1000.0
                    return temp
        except Exception:
            pass
        return None
    
    def emergency_stop_all_recording(self):
        """Emergency stop all active recordings."""
        try:
            active_channels = [ch for ch, active in self.recording_active.items() if active]
            
            for pin in active_channels:
                self.recording_active[pin] = False
                channel = self._pin_to_channel(pin, ButtonType.RECORDING)
                callback = self._get_recording_callback(channel)
                if callback:
                    callback(channel, "emergency_stop")
            
            self.logger.warning(f"Emergency stopped {len(active_channels)} active recordings")
            
        except Exception as e:
            self.logger.error(f"Error in emergency stop: {e}")
    
    def validate_gpio_configuration(self) -> List[str]:
        """Validate GPIO configuration and return any issues."""
        issues = []
        
        # Check for duplicate pins
        all_pins = list(self.recording_pins.values()) + list(self.playback_pins.values())
        if len(all_pins) != len(set(all_pins)):
            issues.append("Duplicate GPIO pins detected in configuration")
        
        # Check for valid pin numbers (BCM mode)
        valid_pins = list(range(2, 28))  # BCM pins 2-27
        for pin in all_pins:
            if pin not in valid_pins:
                issues.append(f"Invalid GPIO pin number: {pin}")
        
        # Check that all required channels are configured
        for channel in range(1, 6):
            rec_pin = self.recording_pins.get(f"REC_BUTTON_{self._channel_to_name(channel)}")
            play_pin = self.playback_pins.get(f"PHONE_UP_{self._channel_to_name(channel)}")
            
            if rec_pin is None:
                issues.append(f"Missing recording button configuration for channel {channel}")
            if play_pin is None:
                issues.append(f"Missing playback button configuration for channel {channel}")
        
        return issues
    
    def cleanup(self):
        """Clean up GPIO resources."""
        try:
            self.stop_monitoring_buttons()
            
            # Clean up gpiozero buttons
            if RASPBERRY_PI:
                for button in self.recording_buttons.values():
                    try:
                        button.close()
                    except Exception as e:
                        self.logger.warning(f"Error closing recording button: {e}")
                
                for button in self.playback_buttons.values():
                    try:
                        button.close()
                    except Exception as e:
                        self.logger.warning(f"Error closing playback button: {e}")
            
            # Clean up RPi.GPIO
            GPIO.cleanup()
            
            self.logger.info("GPIO cleanup completed")
            
        except Exception as e:
            self.logger.error(f"GPIO cleanup failed: {e}")