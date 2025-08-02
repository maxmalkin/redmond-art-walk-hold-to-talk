#!/bin/bash

# spchcat Installation Script for Raspberry Pi Audio System
# MANDATORY: This installs the ONLY approved speech-to-text engine for the system
# spchcat is a pre-built package for Linux/Raspberry Pi available from petewarden/spchcat

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
SPCHCAT_REPO="https://github.com/petewarden/spchcat"
SPCHCAT_BINARY_URL_X86="https://github.com/petewarden/spchcat/releases/download/v0.1.0/spchcat_linux_x86_64"
SPCHCAT_BINARY_URL_ARM="https://github.com/petewarden/spchcat/releases/download/v0.1.0/spchcat_linux_arm64"
SPCHCAT_BINARY_URL_ARM32="https://github.com/petewarden/spchcat/releases/download/v0.1.0/spchcat_linux_armv7"
INSTALL_PREFIX="/usr/local"
TEMP_DIR="/tmp/spchcat_install"

# Detect system architecture
detect_architecture() {
    local arch=$(uname -m)
    local binary_url=""
    
    case $arch in
        x86_64)
            binary_url="$SPCHCAT_BINARY_URL_X86"
            info "Detected x86_64 architecture"
            ;;
        aarch64|arm64)
            binary_url="$SPCHCAT_BINARY_URL_ARM"
            info "Detected ARM64 architecture"
            ;;
        armv7l|arm*)
            binary_url="$SPCHCAT_BINARY_URL_ARM32"
            info "Detected ARM32 architecture"
            ;;
        *)
            error "Unsupported architecture: $arch"
            error "spchcat is available for x86_64, arm64, and armv7 only"
            exit 1
            ;;
    esac
    
    echo "$binary_url"
}

# Check system requirements
check_requirements() {
    log "Checking system requirements for spchcat..."
    
    # Check for wget or curl
    if ! command -v wget &> /dev/null && ! command -v curl &> /dev/null; then
        error "Neither wget nor curl found. Please install one of them:"
        error "sudo apt-get install wget"
        exit 1
    fi
    
    # Check for PulseAudio (required by spchcat)
    if ! command -v pulseaudio &> /dev/null; then
        warn "PulseAudio not found. spchcat requires PulseAudio for audio input."
        warn "Install with: sudo apt-get install pulseaudio"
        warn "You may also need: sudo apt-get install pulseaudio-utils"
    fi
    
    # Check if we can write to install directory
    if [ ! -w "$(dirname "$INSTALL_PREFIX/bin")" ] && [ "$EUID" -ne 0 ]; then
        error "Cannot write to $INSTALL_PREFIX/bin. Please run with sudo or choose different install location."
        info "This script will use sudo for installation when needed."
    fi
    
    log "System requirements check completed"
}

# Check if spchcat is already installed
check_existing_installation() {
    if command -v spchcat &> /dev/null; then
        log "spchcat is already installed at $(which spchcat)"
        
        # Test if it works
        if spchcat --help &> /dev/null; then
            log "Existing spchcat installation appears to be working"
            read -p "Reinstall spchcat? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log "Using existing spchcat installation"
                return 0
            fi
        else
            warn "Existing spchcat installation may be corrupted"
            info "Proceeding with reinstallation..."
        fi
    fi
    
    return 1
}

# Create temporary directory
setup_temp_directory() {
    log "Setting up temporary directory..."
    
    if [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
    
    mkdir -p "$TEMP_DIR"
    cd "$TEMP_DIR"
    
    log "Using temporary directory: $TEMP_DIR"
}

# Download spchcat binary
download_spchcat() {
    local binary_url="$1"
    
    log "Downloading spchcat binary from: $binary_url"
    
    if command -v wget &> /dev/null; then
        wget -O spchcat "$binary_url"
    elif command -v curl &> /dev/null; then
        curl -L -o spchcat "$binary_url"
    else
        error "Neither wget nor curl available for download"
        exit 1
    fi
    
    if [ ! -f "spchcat" ]; then
        error "Failed to download spchcat binary"
        exit 1
    fi
    
    # Verify it's a valid binary
    if ! file spchcat | grep -q "executable"; then
        error "Downloaded file is not a valid executable"
        exit 1
    fi
    
    log "spchcat binary downloaded successfully"
}

# Install spchcat binary
install_spchcat() {
    log "Installing spchcat to $INSTALL_PREFIX/bin..."
    
    # Make binary executable
    chmod +x spchcat
    
    # Create bin directory if it doesn't exist
    sudo mkdir -p "$INSTALL_PREFIX/bin"
    
    # Install binary
    sudo cp spchcat "$INSTALL_PREFIX/bin/spchcat"
    
    # Verify installation
    if [ -f "$INSTALL_PREFIX/bin/spchcat" ]; then
        log "spchcat installed successfully to $INSTALL_PREFIX/bin/spchcat"
    else
        error "Failed to install spchcat binary"
        exit 1
    fi
    
    # Make sure it's in PATH
    if ! command -v spchcat &> /dev/null; then
        warn "spchcat not found in PATH"
        warn "You may need to add $INSTALL_PREFIX/bin to your PATH"
        warn "Add this to your ~/.bashrc: export PATH=\"$INSTALL_PREFIX/bin:\$PATH\""
    else
        log "spchcat is available in PATH"
    fi
}

# Test spchcat installation
test_installation() {
    log "Testing spchcat installation..."
    
    # Test help command
    if spchcat --help &> /dev/null; then
        log "spchcat help command works"
    else
        error "spchcat help command failed"
        exit 1
    fi
    
    # Test with a simple command (this might fail if no audio setup, but that's ok)
    log "Testing spchcat basic functionality..."
    
    # Create a simple test to see if spchcat runs without crashing
    timeout 2s spchcat --source=system --language=en &> /dev/null || true
    
    if [ $? -eq 124 ]; then
        log "spchcat appears to be working (test timed out as expected)"
    else
        warn "spchcat test had unexpected result (may be normal without audio setup)"
    fi
    
    log "spchcat installation test completed"
}

# Setup spchcat configuration for the audio system
setup_configuration() {
    log "Setting up spchcat configuration for audio system..."
    
    # Create configuration directory for our system
    local config_dir="$HOME/.config/raspberry_pi_audio_system"
    mkdir -p "$config_dir"
    
    # Create spchcat usage notes
    cat > "$config_dir/spchcat_usage.txt" <<EOF
spchcat Usage for Raspberry Pi Audio System
==========================================

Basic Commands:
- Transcribe from microphone: spchcat
- Transcribe from file: spchcat /path/to/audio.wav
- Specify language: spchcat --language=en
- Output to file: spchcat > transcript.txt

Supported Languages (examples):
- English: en
- Spanish: es
- French: fr
- German: de
- Italian: it
- Portuguese: pt

Audio Sources:
- Default microphone: spchcat (default)
- System audio: spchcat --source=system
- Specific file: spchcat /path/to/file.wav

Integration Notes:
- spchcat outputs text to stdout by default
- Use subprocess to capture output in Python
- spchcat requires PulseAudio for audio input
- Works with WAV files (16-bit, mono recommended)

Performance Tips for Raspberry Pi:
- Close unnecessary applications during transcription
- Ensure good microphone quality for better accuracy
- Consider using shorter audio clips for better performance
- Monitor system temperature during heavy usage
EOF
    
    log "spchcat configuration and usage notes created in $config_dir"
}

# Cleanup function
cleanup() {
    if [ -d "$TEMP_DIR" ]; then
        log "Cleaning up temporary directory..."
        rm -rf "$TEMP_DIR"
    fi
}

# Main installation function
main() {
    log "Starting spchcat installation for Raspberry Pi Audio System..."
    log "MANDATORY: spchcat is the ONLY approved speech-to-text engine"
    echo
    info "spchcat GitHub repository: $SPCHCAT_REPO"
    echo
    
    # Check if already installed
    if check_existing_installation; then
        log "spchcat installation skipped - using existing installation"
        return 0
    fi
    
    # Detect architecture and get download URL
    local binary_url=$(detect_architecture)
    
    # Check system requirements
    check_requirements
    
    # Setup cleanup trap
    trap cleanup EXIT
    
    # Setup temporary directory
    setup_temp_directory
    
    # Download spchcat binary
    download_spchcat "$binary_url"
    
    # Install spchcat
    install_spchcat
    
    # Test installation
    test_installation
    
    # Setup configuration
    setup_configuration
    
    log "spchcat installation completed successfully!"
    echo
    info "spchcat has been installed to: $INSTALL_PREFIX/bin/spchcat"
    info "Configuration notes: $HOME/.config/raspberry_pi_audio_system/spchcat_usage.txt"
    echo
    info "You can test spchcat with: spchcat --help"
    info "For audio transcription: spchcat (speaks into microphone)"
    echo
    
    # Final verification
    if ! command -v spchcat &> /dev/null; then
        error "spchcat installation verification failed!"
        error "spchcat not found in PATH. You may need to:"
        error "1. Add $INSTALL_PREFIX/bin to your PATH"
        error "2. Restart your terminal session"
        exit 1
    fi
    
    log "spchcat is ready for use with the Raspberry Pi Audio System"
    
    # Show version info if available
    if spchcat --help 2>&1 | grep -q "version\|Version"; then
        info "Installed spchcat version information:"
        spchcat --help 2>&1 | grep -i version || true
    fi
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root"
    error "The script will use sudo when needed for installation"
    exit 1
fi

# Ensure we're in a reasonable directory context
if [ ! -f "../config.yaml" ] && [ ! -f "config.yaml" ]; then
    warn "This script should ideally be run from the raspberry_pi_audio_system directory"
    warn "Current directory: $(pwd)"
    info "Continuing with installation anyway..."
fi

# Run main installation
main "$@"