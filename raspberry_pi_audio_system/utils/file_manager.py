"""
File Manager for Raspberry Pi Audio System.

Handles file organization, cleanup, and management operations
across the audio processing pipeline with channel mapping preservation.
"""

import os
import shutil
import logging
import glob
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import json
import wave


class FileManager:
    """
    File management system for audio recording and processing.
    
    Manages file organization across recording pipeline while preserving
    channel mapping and providing cleanup and maintenance operations.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize file manager with configuration.
        
        Args:
            config: Configuration dictionary containing file paths
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Extract file paths from configuration
        self.paths = config.get('paths', {})
        self.recordings_dir = self.paths.get('recordings', './recordings')
        self.temp_dir = self.paths.get('temp', './temp')
        self.bin_dir = self.paths.get('bin', './bin')
        self.playable_dir = self.paths.get('playable', './playable')
        self.logs_dir = self.paths.get('logs', './logs')
        self.backup_dir = self.paths.get('backup', './backup')
        
        # File management settings
        self.file_config = config.get('file_management', {})
        self.max_temp_age_hours = self.file_config.get('max_temp_age_hours', 1)
        self.max_log_age_days = self.file_config.get('max_log_age_days', 30)
        self.backup_enabled = self.file_config.get('backup_enabled', True)
        self.max_files_per_channel = self.file_config.get('max_files_per_channel', 100)
        
        # Initialize directory structure
        self._create_directory_structure()
    
    def _create_directory_structure(self):
        """Create complete directory structure for the system."""
        try:
            # Main directories
            directories = [
                self.recordings_dir,
                self.temp_dir,
                self.bin_dir,
                self.playable_dir,
                self.logs_dir,
                self.backup_dir
            ]
            
            for directory in directories:
                os.makedirs(directory, exist_ok=True)
            
            # Channel-specific subdirectories
            for channel in range(1, 6):
                channel_dirs = [
                    os.path.join(self.bin_dir, f"channel_{channel}"),
                    os.path.join(self.playable_dir, f"channel_{channel}"),
                    os.path.join(self.backup_dir, f"channel_{channel}")
                ]
                
                for channel_dir in channel_dirs:
                    os.makedirs(channel_dir, exist_ok=True)
            
            self.logger.info("File directory structure created successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to create directory structure: {e}")
            raise
    
    def get_channel_directory(self, channel: int, directory_type: str) -> str:
        """
        Get directory path for specific channel and type.
        
        Args:
            channel: Channel number (1-5)
            directory_type: Type of directory ('bin', 'playable', 'backup')
            
        Returns:
            Directory path for channel
        """
        base_dirs = {
            'bin': self.bin_dir,
            'playable': self.playable_dir,
            'backup': self.backup_dir
        }
        
        base_dir = base_dirs.get(directory_type)
        if not base_dir:
            raise ValueError(f"Invalid directory type: {directory_type}")
        
        return os.path.join(base_dir, f"channel_{channel}")
    
    def get_file_info(self, file_path: str) -> Optional[Dict]:
        """
        Get detailed information about an audio file.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            File information dictionary or None if file not found
        """
        try:
            if not os.path.exists(file_path):
                return None
            
            # Basic file information
            stat = os.stat(file_path)
            info = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'size': stat.st_size,
                'created_time': datetime.fromtimestamp(stat.st_ctime),
                'modified_time': datetime.fromtimestamp(stat.st_mtime),
                'accessed_time': datetime.fromtimestamp(stat.st_atime)
            }
            
            # Audio file specific information
            if file_path.endswith('.wav'):
                try:
                    with wave.open(file_path, 'rb') as wav_file:
                        info.update({
                            'duration': wav_file.getnframes() / wav_file.getframerate(),
                            'sample_rate': wav_file.getframerate(),
                            'channels': wav_file.getnchannels(),
                            'sample_width': wav_file.getsampwidth(),
                            'frames': wav_file.getnframes()
                        })
                except Exception as e:
                    self.logger.warning(f"Could not read audio info from {file_path}: {e}")
            
            # Determine channel from file path
            channel = self._extract_channel_from_path(file_path)
            if channel:
                info['channel'] = channel
            
            return info
            
        except Exception as e:
            self.logger.error(f"Failed to get file info for {file_path}: {e}")
            return None
    
    def _extract_channel_from_path(self, file_path: str) -> Optional[int]:
        """Extract channel number from file path."""
        try:
            # Check if path contains channel directory
            for channel in range(1, 6):
                if f"channel_{channel}" in file_path:
                    return channel
            
            # Check if filename contains channel info
            filename = os.path.basename(file_path)
            if '_ch' in filename:
                parts = filename.split('_ch')
                if len(parts) > 1:
                    channel_part = parts[1].split('_')[0]
                    return int(channel_part)
            
            return None
            
        except Exception:
            return None
    
    def move_file(self, source_path: str, destination_dir: str, preserve_name: bool = True) -> Optional[str]:
        """
        Move file to destination directory.
        
        Args:
            source_path: Source file path
            destination_dir: Destination directory
            preserve_name: Whether to preserve original filename
            
        Returns:
            New file path or None if move failed
        """
        try:
            if not os.path.exists(source_path):
                self.logger.error(f"Source file not found: {source_path}")
                return None
            
            os.makedirs(destination_dir, exist_ok=True)
            
            if preserve_name:
                filename = os.path.basename(source_path)
            else:
                # Generate new filename with timestamp
                base, ext = os.path.splitext(os.path.basename(source_path))
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{base}_{timestamp}{ext}"
            
            dest_path = os.path.join(destination_dir, filename)
            
            # Handle filename conflicts
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(dest_path):
                    new_filename = f"{base}_{counter}{ext}"
                    dest_path = os.path.join(destination_dir, new_filename)
                    counter += 1
            
            shutil.move(source_path, dest_path)
            self.logger.info(f"Moved file from {source_path} to {dest_path}")
            return dest_path
            
        except Exception as e:
            self.logger.error(f"Failed to move file {source_path}: {e}")
            return None
    
    def copy_file(self, source_path: str, destination_dir: str, new_name: Optional[str] = None) -> Optional[str]:
        """
        Copy file to destination directory.
        
        Args:
            source_path: Source file path
            destination_dir: Destination directory
            new_name: Optional new filename
            
        Returns:
            New file path or None if copy failed
        """
        try:
            if not os.path.exists(source_path):
                self.logger.error(f"Source file not found: {source_path}")
                return None
            
            os.makedirs(destination_dir, exist_ok=True)
            
            filename = new_name if new_name else os.path.basename(source_path)
            dest_path = os.path.join(destination_dir, filename)
            
            # Handle filename conflicts
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(dest_path):
                    new_filename = f"{base}_{counter}{ext}"
                    dest_path = os.path.join(destination_dir, new_filename)
                    counter += 1
            
            shutil.copy2(source_path, dest_path)
            self.logger.info(f"Copied file from {source_path} to {dest_path}")
            return dest_path
            
        except Exception as e:
            self.logger.error(f"Failed to copy file {source_path}: {e}")
            return None
    
    def delete_file(self, file_path: str) -> bool:
        """
        Delete file safely.
        
        Args:
            file_path: Path to file to delete
            
        Returns:
            True if file was deleted successfully
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                self.logger.info(f"Deleted file: {file_path}")
                return True
            else:
                self.logger.warning(f"File not found for deletion: {file_path}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to delete file {file_path}: {e}")
            return False
    
    def cleanup_temp_files(self) -> int:
        """
        Clean up old temporary files.
        
        Returns:
            Number of files cleaned up
        """
        try:
            cutoff_time = datetime.now() - timedelta(hours=self.max_temp_age_hours)
            cleaned_count = 0
            
            # Find old temporary files
            temp_files = glob.glob(os.path.join(self.temp_dir, "*"))
            
            for file_path in temp_files:
                try:
                    if os.path.isfile(file_path):
                        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                        if file_time < cutoff_time:
                            os.remove(file_path)
                            cleaned_count += 1
                            self.logger.debug(f"Cleaned up temp file: {file_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to clean up {file_path}: {e}")
            
            if cleaned_count > 0:
                self.logger.info(f"Cleaned up {cleaned_count} temporary files")
            
            return cleaned_count
            
        except Exception as e:
            self.logger.error(f"Temporary file cleanup failed: {e}")
            return 0
    
    def cleanup_old_logs(self) -> int:
        """
        Clean up old log files.
        
        Returns:
            Number of log files cleaned up
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=self.max_log_age_days)
            cleaned_count = 0
            
            # Find old log files
            log_files = glob.glob(os.path.join(self.logs_dir, "*.log"))
            
            for file_path in log_files:
                try:
                    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if file_time < cutoff_time:
                        os.remove(file_path)
                        cleaned_count += 1
                        self.logger.debug(f"Cleaned up old log: {file_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to clean up log {file_path}: {e}")
            
            if cleaned_count > 0:
                self.logger.info(f"Cleaned up {cleaned_count} old log files")
            
            return cleaned_count
            
        except Exception as e:
            self.logger.error(f"Log cleanup failed: {e}")
            return 0
    
    def manage_channel_files(self, channel: int) -> Dict[str, int]:
        """
        Manage files for specific channel (remove oldest if over limit).
        
        Args:
            channel: Channel number (1-5)
            
        Returns:
            Dictionary with cleanup statistics
        """
        try:
            results = {'playable_removed': 0, 'bin_removed': 0}
            
            # Manage playable files
            playable_dir = self.get_channel_directory(channel, 'playable')
            playable_files = glob.glob(os.path.join(playable_dir, "*.wav"))
            
            if len(playable_files) > self.max_files_per_channel:
                # Sort by modification time (oldest first)
                playable_files.sort(key=lambda x: os.path.getmtime(x))
                files_to_remove = len(playable_files) - self.max_files_per_channel
                
                for file_path in playable_files[:files_to_remove]:
                    if self.delete_file(file_path):
                        results['playable_removed'] += 1
            
            # Manage bin files
            bin_dir = self.get_channel_directory(channel, 'bin')
            bin_files = glob.glob(os.path.join(bin_dir, "*.wav"))
            
            if len(bin_files) > self.max_files_per_channel:
                # Sort by modification time (oldest first)
                bin_files.sort(key=lambda x: os.path.getmtime(x))
                files_to_remove = len(bin_files) - self.max_files_per_channel
                
                for file_path in bin_files[:files_to_remove]:
                    if self.delete_file(file_path):
                        results['bin_removed'] += 1
            
            if results['playable_removed'] > 0 or results['bin_removed'] > 0:
                self.logger.info(f"Channel {channel} file management: {results}")
            
            return results
            
        except Exception as e:
            self.logger.error(f"Channel {channel} file management failed: {e}")
            return {'playable_removed': 0, 'bin_removed': 0}
    
    def backup_channel_files(self, channel: int) -> bool:
        """
        Backup files for specific channel.
        
        Args:
            channel: Channel number (1-5)
            
        Returns:
            True if backup was successful
        """
        if not self.backup_enabled:
            return True
        
        try:
            backup_channel_dir = self.get_channel_directory(channel, 'backup')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Backup playable files
            playable_dir = self.get_channel_directory(channel, 'playable')
            playable_backup_dir = os.path.join(backup_channel_dir, f"playable_{timestamp}")
            
            if os.path.exists(playable_dir):
                shutil.copytree(playable_dir, playable_backup_dir, ignore_errors=True)
            
            # Backup bin files (optional, may be large)
            bin_dir = self.get_channel_directory(channel, 'bin')
            bin_backup_dir = os.path.join(backup_channel_dir, f"bin_{timestamp}")
            
            if os.path.exists(bin_dir):
                shutil.copytree(bin_dir, bin_backup_dir, ignore_errors=True)
            
            self.logger.info(f"Backup completed for channel {channel}")
            return True
            
        except Exception as e:
            self.logger.error(f"Backup failed for channel {channel}: {e}")
            return False
    
    def get_disk_usage(self) -> Dict[str, Dict]:
        """
        Get disk usage information for all managed directories.
        
        Returns:
            Dictionary with disk usage information
        """
        try:
            usage_info = {}
            
            directories = {
                'recordings': self.recordings_dir,
                'temp': self.temp_dir,
                'bin': self.bin_dir,
                'playable': self.playable_dir,
                'logs': self.logs_dir,
                'backup': self.backup_dir
            }
            
            for name, directory in directories.items():
                if os.path.exists(directory):
                    size = self._get_directory_size(directory)
                    file_count = self._count_files_in_directory(directory)
                    
                    usage_info[name] = {
                        'path': directory,
                        'size_bytes': size,
                        'size_mb': round(size / (1024 * 1024), 2),
                        'file_count': file_count
                    }
                else:
                    usage_info[name] = {
                        'path': directory,
                        'size_bytes': 0,
                        'size_mb': 0,
                        'file_count': 0
                    }
            
            return usage_info
            
        except Exception as e:
            self.logger.error(f"Failed to get disk usage: {e}")
            return {}
    
    def _get_directory_size(self, directory: str) -> int:
        """Get total size of directory in bytes."""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(file_path)
                    except OSError:
                        pass
        except Exception:
            pass
        return total_size
    
    def _count_files_in_directory(self, directory: str) -> int:
        """Count total number of files in directory."""
        file_count = 0
        try:
            for dirpath, dirnames, filenames in os.walk(directory):
                file_count += len(filenames)
        except Exception:
            pass
        return file_count
    
    def perform_maintenance(self) -> Dict[str, int]:
        """
        Perform routine file maintenance tasks.
        
        Returns:
            Dictionary with maintenance results
        """
        try:
            results = {
                'temp_files_cleaned': 0,
                'log_files_cleaned': 0,
                'channels_managed': 0
            }
            
            # Clean up temporary files
            results['temp_files_cleaned'] = self.cleanup_temp_files()
            
            # Clean up old logs
            results['log_files_cleaned'] = self.cleanup_old_logs()
            
            # Manage files for each channel
            for channel in range(1, 6):
                channel_results = self.manage_channel_files(channel)
                if channel_results['playable_removed'] > 0 or channel_results['bin_removed'] > 0:
                    results['channels_managed'] += 1
            
            self.logger.info(f"File maintenance completed: {results}")
            return results
            
        except Exception as e:
            self.logger.error(f"File maintenance failed: {e}")
            return {'temp_files_cleaned': 0, 'log_files_cleaned': 0, 'channels_managed': 0}
    
    def get_system_status(self) -> Dict:
        """
        Get overall file system status.
        
        Returns:
            Dictionary with system status information
        """
        try:
            status = {
                'disk_usage': self.get_disk_usage(),
                'directories_exist': {},
                'channel_file_counts': {}
            }
            
            # Check directory existence
            directories = [
                self.recordings_dir, self.temp_dir, self.bin_dir,
                self.playable_dir, self.logs_dir, self.backup_dir
            ]
            
            for directory in directories:
                status['directories_exist'][os.path.basename(directory)] = os.path.exists(directory)
            
            # Get file counts per channel
            for channel in range(1, 6):
                playable_dir = self.get_channel_directory(channel, 'playable')
                bin_dir = self.get_channel_directory(channel, 'bin')
                
                playable_count = len(glob.glob(os.path.join(playable_dir, "*.wav")))
                bin_count = len(glob.glob(os.path.join(bin_dir, "*.wav")))
                
                status['channel_file_counts'][f'channel_{channel}'] = {
                    'playable': playable_count,
                    'bin': bin_count
                }
            
            return status
            
        except Exception as e:
            self.logger.error(f"Failed to get system status: {e}")
            return {}
    
    def cleanup(self):
        """Clean up file manager resources."""
        try:
            # Perform final maintenance
            self.perform_maintenance()
            self.logger.info("File manager cleanup completed")
        except Exception as e:
            self.logger.error(f"File manager cleanup failed: {e}")