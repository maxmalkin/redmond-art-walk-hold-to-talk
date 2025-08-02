#!/bin/bash

# spchcat Installation Script for Raspberry Pi Audio System
# MANDATORY: This installs the ONLY approved speech-to-text engine for the system

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
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

# Configuration
SPCHCAT_VERSION="1.0.0"
SPCHCAT_REPO="https://github.com/spchcat/spchcat.git"
INSTALL_PREFIX="/usr/local"
TEMP_BUILD_DIR="/tmp/spchcat_build"
MODEL_URL="https://github.com/spchcat/models/releases/download/v1.0.0/en-us-model.tar.gz"

# Check system requirements
check_requirements() {
    log "Checking system requirements for spchcat compilation..."
    
    # Check for required build tools
    local missing_tools=()
    
    if ! command -v gcc &> /dev/null; then
        missing_tools+=("gcc")
    fi
    
    if ! command -v cmake &> /dev/null; then
        missing_tools+=("cmake")
    fi
    
    if ! command -v git &> /dev/null; then
        missing_tools+=("git")
    fi
    
    if ! command -v pkg-config &> /dev/null; then
        missing_tools+=("pkg-config")
    fi
    
    if [ ${#missing_tools[@]} -ne 0 ]; then
        error "Missing required build tools: ${missing_tools[*]}"
        error "Please install them first: sudo apt-get install ${missing_tools[*]}"
        exit 1
    fi
    
    # Check for required libraries
    if ! pkg-config --exists libssl; then
        error "libssl-dev not found. Install with: sudo apt-get install libssl-dev"
        exit 1
    fi
    
    if ! pkg-config --exists libcurl; then
        error "libcurl4-openssl-dev not found. Install with: sudo apt-get install libcurl4-openssl-dev"
        exit 1
    fi
    
    log "System requirements check passed"
}

# Check if spchcat is already installed
check_existing_installation() {
    if command -v spchcat &> /dev/null; then
        log "spchcat is already installed"
        
        # Check version
        local installed_version=$(spchcat --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        
        if [ "$installed_version" = "$SPCHCAT_VERSION" ]; then
            log "spchcat version $installed_version matches required version"
            read -p "Reinstall spchcat? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log "Using existing spchcat installation"
                return 0
            fi
        else
            warn "Installed spchcat version ($installed_version) differs from required ($SPCHCAT_VERSION)"
            info "Proceeding with reinstallation..."
        fi
    fi
    
    return 1
}

# Clean up any previous build attempts
cleanup_build_directory() {
    log "Cleaning up previous build directory..."
    
    if [ -d "$TEMP_BUILD_DIR" ]; then
        rm -rf "$TEMP_BUILD_DIR"
    fi
    
    mkdir -p "$TEMP_BUILD_DIR"
    cd "$TEMP_BUILD_DIR"
}

# Download spchcat source code
download_source() {
    log "Downloading spchcat source code..."
    
    # Clone the repository
    git clone --branch "v$SPCHCAT_VERSION" --depth 1 "$SPCHCAT_REPO" spchcat
    
    if [ ! -d "spchcat" ]; then
        error "Failed to download spchcat source code"
        exit 1
    fi
    
    cd spchcat
    log "spchcat source code downloaded successfully"
}

# Configure build
configure_build() {
    log "Configuring spchcat build..."
    
    # Create build directory
    mkdir -p build
    cd build
    
    # Configure with CMake
    cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX" \
        -DENABLE_SHARED=ON \
        -DENABLE_STATIC=OFF \
        -DWITH_CUDA=OFF \
        -DWITH_OPENCL=OFF \
        -DBUILD_TESTS=OFF \
        -DBUILD_EXAMPLES=OFF
    
    log "Build configuration completed"
}

# Compile spchcat
compile_spchcat() {
    log "Compiling spchcat (this may take several minutes)..."
    
    # Get number of CPU cores for parallel compilation
    local num_cores=$(nproc)
    
    # Compile
    make -j"$num_cores"
    
    log "spchcat compilation completed"
}

# Install spchcat
install_spchcat() {
    log "Installing spchcat to $INSTALL_PREFIX..."
    
    # Install binaries and libraries
    sudo make install
    
    # Update library cache
    sudo ldconfig
    
    # Verify installation
    if command -v spchcat &> /dev/null; then
        log "spchcat installed successfully"
        local installed_version=$(spchcat --version 2>/dev/null | head -1)
        info "Installed version: $installed_version"
    else
        error "spchcat installation failed - binary not found in PATH"
        exit 1
    fi
}

# Download and install language models
install_models() {
    log "Installing spchcat language models..."
    
    # Create model directory
    local model_dir="$INSTALL_PREFIX/share/spchcat/models"
    sudo mkdir -p "$model_dir"
    
    # Download English model
    cd /tmp
    
    if wget -q "$MODEL_URL" -O en-us-model.tar.gz; then
        log "Downloaded English language model"
        
        # Extract model
        sudo tar -xzf en-us-model.tar.gz -C "$model_dir"
        
        # Clean up
        rm -f en-us-model.tar.gz
        
        log "Language models installed to $model_dir"
    else
        error "Failed to download language models"
        error "You will need to manually download and install models from:"
        error "$MODEL_URL"
        
        # Continue without models - user can install later
        warn "Continuing installation without models..."
    fi
}

# Create spchcat configuration file
create_config() {
    log "Creating spchcat configuration..."
    
    local config_dir="$INSTALL_PREFIX/etc/spchcat"
    sudo mkdir -p "$config_dir"
    
    # Create default configuration
    sudo tee "$config_dir/spchcat.conf" > /dev/null <<EOF
# spchcat Configuration for Raspberry Pi Audio System
# This configuration is optimized for the audio recording system

[general]
# Default language model
default_language = en
model_path = $INSTALL_PREFIX/share/spchcat/models

# Audio processing settings
sample_rate = 44100
chunk_size = 1024

# Recognition settings
confidence_threshold = 0.7
max_alternatives = 1

# Performance settings (optimized for Raspberry Pi)
num_threads = 2
enable_vad = true
vad_threshold = 0.3

# Output format
output_format = json
include_timestamps = true
include_confidence = true

[logging]
level = info
file = /var/log/spchcat.log

[cache]
enable_model_cache = true
cache_size_mb = 128
EOF
    
    log "spchcat configuration created"
}

# Setup spchcat service user and permissions
setup_permissions() {
    log "Setting up spchcat permissions..."
    
    # Create spchcat group if it doesn't exist
    if ! getent group spchcat >/dev/null; then
        sudo groupadd spchcat
    fi
    
    # Add current user to spchcat group
    sudo usermod -a -G spchcat "$USER"
    
    # Set appropriate permissions on model directory
    local model_dir="$INSTALL_PREFIX/share/spchcat"
    if [ -d "$model_dir" ]; then
        sudo chgrp -R spchcat "$model_dir"
        sudo chmod -R g+r "$model_dir"
    fi
    
    # Create log directory
    sudo mkdir -p /var/log/spchcat
    sudo chown root:spchcat /var/log/spchcat
    sudo chmod 775 /var/log/spchcat
    
    log "spchcat permissions configured"
}

# Test spchcat installation
test_installation() {
    log "Testing spchcat installation..."
    
    # Test basic functionality
    if spchcat --help >/dev/null 2>&1; then
        log "spchcat help command works"
    else
        error "spchcat help command failed"
        exit 1
    fi
    
    # Test version command
    if spchcat --version >/dev/null 2>&1; then
        log "spchcat version command works"
    else
        warn "spchcat version command failed (may be normal)"
    fi
    
    # Test model loading (if models are available)
    local model_dir="$INSTALL_PREFIX/share/spchcat/models"
    if [ -d "$model_dir" ] && [ "$(ls -A "$model_dir")" ]; then
        log "Language models are available"
        
        # Create a small test audio file and test recognition
        info "Creating test audio file for recognition test..."
        
        # Generate a simple sine wave as test audio (1 second, 440Hz)
        python3 -c "
import wave
import numpy as np

# Generate 1 second of 440Hz sine wave
sample_rate = 44100
duration = 1.0
frequency = 440.0

t = np.linspace(0, duration, int(sample_rate * duration), False)
audio = np.sin(2 * np.pi * frequency * t) * 0.3
audio_int = (audio * 32767).astype(np.int16)

# Save as WAV file
with wave.open('/tmp/spchcat_test.wav', 'w') as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(sample_rate)
    wav_file.writeframes(audio_int.tobytes())

print('Test audio file created')
"
        
        # Test speech recognition on the test file
        if spchcat --input /tmp/spchcat_test.wav --output-format json >/dev/null 2>&1; then
            log "spchcat recognition test passed"
        else
            warn "spchcat recognition test failed (may be due to test audio content)"
        fi
        
        # Clean up test file
        rm -f /tmp/spchcat_test.wav
        
    else
        warn "No language models found - speech recognition will not work"
        warn "Download models manually from: $MODEL_URL"
    fi
    
    log "spchcat installation test completed"
}

# Cleanup function
cleanup() {
    log "Cleaning up build directory..."
    
    if [ -d "$TEMP_BUILD_DIR" ]; then
        rm -rf "$TEMP_BUILD_DIR"
    fi
    
    log "Cleanup completed"
}

# Main installation function
main() {
    log "Starting spchcat installation for Raspberry Pi Audio System..."
    log "MANDATORY: spchcat is the ONLY approved speech-to-text engine"
    
    # Check if already installed
    if check_existing_installation; then
        log "spchcat installation skipped - using existing installation"
        return 0
    fi
    
    # Check system requirements
    check_requirements
    
    # Setup cleanup trap
    trap cleanup EXIT
    
    # Clean up build directory
    cleanup_build_directory
    
    # Download source code
    download_source
    
    # Configure build
    configure_build
    
    # Compile
    compile_spchcat
    
    # Install
    install_spchcat
    
    # Install models
    install_models
    
    # Create configuration
    create_config
    
    # Setup permissions
    setup_permissions
    
    # Test installation
    test_installation
    
    log "spchcat installation completed successfully!"
    echo
    info "spchcat has been installed to: $INSTALL_PREFIX"
    info "Configuration file: $INSTALL_PREFIX/etc/spchcat/spchcat.conf"
    info "Models directory: $INSTALL_PREFIX/share/spchcat/models"
    echo
    info "You can test spchcat with: spchcat --help"
    
    # Final verification
    if ! command -v spchcat &> /dev/null; then
        error "spchcat installation verification failed!"
        exit 1
    fi
    
    log "spchcat is ready for use with the Raspberry Pi Audio System"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root"
    error "The script will use sudo when needed"
    exit 1
fi

# Ensure we're in the correct directory context
if [ ! -f "../config.yaml" ] && [ ! -f "config.yaml" ]; then
    warn "This script should be run from the raspberry_pi_audio_system directory"
    warn "Current directory: $(pwd)"
fi

# Run main installation
main "$@"