#!/bin/bash

# Raspberry Pi Audio System Installation Script
# This script sets up the complete audio recording and processing system

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO: $1${NC}"
}

# Check if running on Raspberry Pi
check_raspberry_pi() {
    info "Checking if running on Raspberry Pi..."
    
    if [ ! -f /proc/cpuinfo ] || ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        warn "This script is designed for Raspberry Pi, but continuing anyway..."
        read -p "Continue installation? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            error "Installation cancelled by user"
            exit 1
        fi
    else
        log "Raspberry Pi detected"
    fi
}

# Update system packages
update_system() {
    log "Updating system packages..."
    
    sudo apt-get update
    sudo apt-get upgrade -y
    
    log "System packages updated"
}

# Install system dependencies
install_system_dependencies() {
    log "Installing system dependencies..."
    
    # Audio system dependencies
    sudo apt-get install -y \
        python3 \
        python3-pip \
        python3-dev \
        python3-venv \
        libasound2-dev \
        portaudio19-dev \
        libportaudio2 \
        libportaudiocpp0 \
        ffmpeg \
        alsa-utils \
        pulseaudio \
        pulseaudio-utils
    
    # Build tools for spchcat compilation
    sudo apt-get install -y \
        build-essential \
        cmake \
        git \
        pkg-config \
        libssl-dev \
        libcurl4-openssl-dev \
        libjson-c-dev
    
    # GPIO and hardware libraries
    sudo apt-get install -y \
        python3-rpi.gpio \
        i2c-tools \
        spi-tools
    
    # USB audio support
    sudo apt-get install -y \
        usb-modeswitch \
        usb-modeswitch-data
    
    log "System dependencies installed"
}

# Create virtual environment
create_virtual_environment() {
    log "Creating Python virtual environment..."
    
    # Create virtual environment
    python3 -m venv venv
    
    # Activate virtual environment
    source venv/bin/activate
    
    # Upgrade pip
    pip install --upgrade pip setuptools wheel
    
    log "Virtual environment created and activated"
}

# Install Python dependencies
install_python_dependencies() {
    log "Installing Python dependencies..."
    
    # Ensure virtual environment is activated
    source venv/bin/activate
    
    # Install requirements
    pip install -r install/requirements.txt
    
    log "Python dependencies installed"
}

# Setup GPIO permissions
setup_gpio_permissions() {
    log "Setting up GPIO permissions..."
    
    # Add user to gpio group
    sudo usermod -a -G gpio $USER
    
    # Set up udev rules for GPIO access
    sudo tee /etc/udev/rules.d/99-gpio.rules > /dev/null <<EOF
KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"
SUBSYSTEM=="gpio", GROUP="gpio", MODE="0660"
EOF
    
    # Reload udev rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    
    log "GPIO permissions configured"
}

# Setup audio permissions and configuration
setup_audio_system() {
    log "Setting up audio system..."
    
    # Add user to audio group
    sudo usermod -a -G audio $USER
    
    # Configure ALSA for USB audio devices
    sudo tee /etc/asound.conf > /dev/null <<EOF
# Default audio configuration for USB devices
pcm.!default {
    type hw
    card 1
}
ctl.!default {
    type hw
    card 1
}

# USB Audio Class 1.0 and 2.0 support
pcm.usb {
    type hw
    card 1
}
EOF
    
    # Configure PulseAudio for USB audio
    mkdir -p ~/.config/pulse
    tee ~/.config/pulse/default.pa > /dev/null <<EOF
# Load audio drivers automatically
.include /etc/pulse/default.pa

# Set default sink to USB audio if available
set-default-sink alsa_output.usb-*
EOF
    
    log "Audio system configured"
}

# Create system directories
create_system_directories() {
    log "Creating system directories..."
    
    # Create required directories
    mkdir -p recordings temp bin playable logs backup
    
    # Create channel subdirectories
    for i in {1..5}; do
        mkdir -p bin/channel_$i
        mkdir -p playable/channel_$i
        mkdir -p backup/channel_$i
    done
    
    # Set appropriate permissions
    chmod 755 recordings temp bin playable logs backup
    
    log "System directories created"
}

# Setup systemd service (optional)
setup_systemd_service() {
    read -p "Would you like to install as a systemd service? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log "Setting up systemd service..."
        
        # Get current directory
        CURRENT_DIR=$(pwd)
        
        # Create systemd service file
        sudo tee /etc/systemd/system/raspberry-pi-audio.service > /dev/null <<EOF
[Unit]
Description=Raspberry Pi Audio Recording and Processing System
After=network.target sound.target
Wants=network.target

[Service]
Type=simple
User=$USER
Group=audio
WorkingDirectory=$CURRENT_DIR
Environment=PATH=$CURRENT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$CURRENT_DIR/venv/bin/python3 main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
        
        # Reload systemd and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable raspberry-pi-audio.service
        
        log "Systemd service installed and enabled"
        info "Use 'sudo systemctl start raspberry-pi-audio' to start the service"
        info "Use 'sudo systemctl status raspberry-pi-audio' to check status"
    else
        info "Skipping systemd service installation"
    fi
}

# Setup logging
setup_logging() {
    log "Setting up logging configuration..."
    
    # Create log directory
    mkdir -p logs
    
    # Set up log rotation
    sudo tee /etc/logrotate.d/raspberry-pi-audio > /dev/null <<EOF
$(pwd)/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 $USER $USER
}
EOF
    
    log "Logging configuration completed"
}

# Run spchcat installation
install_spchcat() {
    log "Installing spchcat (MANDATORY speech-to-text engine)..."
    
    if [ -f "install/spchcat_setup.sh" ]; then
        chmod +x install/spchcat_setup.sh
        ./install/spchcat_setup.sh
    else
        error "spchcat_setup.sh not found! This is required for the system to function."
        exit 1
    fi
    
    log "spchcat installation completed"
}

# Validate installation
validate_installation() {
    log "Validating installation..."
    
    # Check Python dependencies
    source venv/bin/activate
    python3 -c "
try:
    import RPi.GPIO
    print('RPi.GPIO OK')
except ImportError:
    print('RPi.GPIO not available (expected on non-Pi systems)')

try:
    import pyaudio
    print('pyaudio OK')
except ImportError:
    print('pyaudio not available')

try:
    import yaml
    print('PyYAML OK')
except ImportError:
    print('PyYAML not available')

try:
    import numpy
    print('numpy OK')
except ImportError:
    print('numpy not available')

print('Python dependencies check completed')
"
    
    # Check spchcat installation
    if command -v spchcat &> /dev/null; then
        log "spchcat installation validated"
    else
        error "spchcat not found in PATH"
        exit 1
    fi
    
    # Check GPIO access
    if [ -c /dev/gpiochip0 ]; then
        log "GPIO access validated"
    else
        warn "GPIO device not found - may need reboot"
    fi
    
    # Check audio devices
    if aplay -l | grep -q "card"; then
        log "Audio devices detected"
    else
        warn "No audio devices detected"
    fi
    
    log "Installation validation completed"
}

# Create example configuration
create_example_config() {
    if [ ! -f "config.yaml" ]; then
        log "Creating example configuration file..."
        
        # This will be created by the main configuration creation step
        # For now, just inform the user
        info "Default configuration will be created when the system first runs"
    else
        info "Configuration file already exists"
    fi
}

# Main installation function
main() {
    log "Starting Raspberry Pi Audio System installation..."
    
    # Check if we're on a Raspberry Pi
    check_raspberry_pi
    
    # Update system
    update_system
    
    # Install system dependencies
    install_system_dependencies
    
    # Create virtual environment
    create_virtual_environment
    
    # Install Python dependencies
    install_python_dependencies
    
    # Setup permissions and system configuration
    setup_gpio_permissions
    setup_audio_system
    
    # Create system directories
    create_system_directories
    
    # Setup logging
    setup_logging
    
    # Install spchcat (MANDATORY)
    install_spchcat
    
    # Create example configuration
    create_example_config
    
    # Setup systemd service (optional)
    setup_systemd_service
    
    # Validate installation
    validate_installation
    
    log "Installation completed successfully!"
    echo
    info "Next steps:"
    info "1. Review and customize config.yaml"
    info "2. Connect your USB audio devices"
    info "3. Test the system with: python3 main.py --test-mode"
    info "4. Run the system with: python3 main.py"
    info "5. If installed as service, start with: sudo systemctl start raspberry-pi-audio"
    echo
    warn "You may need to reboot for all permissions and drivers to take effect"
    
    read -p "Would you like to reboot now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log "Rebooting system..."
        sudo reboot
    fi
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root"
    error "The script will use sudo when needed"
    exit 1
fi

# Run main installation
main "$@"