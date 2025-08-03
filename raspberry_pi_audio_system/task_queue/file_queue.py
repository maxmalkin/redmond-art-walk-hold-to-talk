"""
File Processing Queue for Raspberry Pi Audio System.

Manages queue-based processing of audio files through the speech-to-text
and content filtering pipeline with proper channel mapping preservation.
"""

import threading
import time
import logging
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from enum import Enum
import queue
import uuid


class TaskStatus(Enum):
    """Task status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """Task priority enumeration."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class ProcessingTask:
    """Represents a single processing task in the queue."""
    
    def __init__(self, task_id: str, channel: int, audio_file: str, metadata: Dict, priority: TaskPriority = TaskPriority.NORMAL):
        self.task_id = task_id
        self.channel = channel
        self.audio_file = audio_file
        self.metadata = metadata
        self.priority = priority
        self.status = TaskStatus.PENDING
        self.created_time = datetime.now()
        self.started_time: Optional[datetime] = None
        self.completed_time: Optional[datetime] = None
        self.error_message: Optional[str] = None
        self.result: Optional[Dict] = None
    
    def __lt__(self, other):
        """Support priority queue ordering."""
        return self.priority.value > other.priority.value  # Higher priority first


class FileProcessingQueue:
    """
    Queue manager for audio file processing pipeline.
    
    Coordinates the flow of audio files through speech processing
    and content filtering while maintaining channel mapping.
    """
    
    def __init__(self, speech_processor, content_filter, config: Dict):
        """
        Initialize file processing queue.
        
        Args:
            speech_processor: SpeechProcessor instance
            content_filter: ContentFilter instance
            config: Configuration dictionary
        """
        self.speech_processor = speech_processor
        self.content_filter = content_filter
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Queue configuration
        self.queue_config = config.get('queue', {})
        self.max_queue_size = self.queue_config.get('max_size', 100)
        self.max_workers = self.queue_config.get('max_workers', 2)
        self.processing_timeout = self.queue_config.get('processing_timeout', 120)
        
        # Processing queue (priority queue)
        self.task_queue = queue.PriorityQueue(maxsize=self.max_queue_size)
        
        # Task tracking
        self.tasks: Dict[str, ProcessingTask] = {}
        self.tasks_lock = threading.Lock()
        
        # Worker threads
        self.workers: List[threading.Thread] = []
        self._stop_event = threading.Event()
        
        # Statistics
        self.stats = {
            'tasks_submitted': 0,
            'tasks_completed': 0,
            'tasks_failed': 0,
            'tasks_cancelled': 0
        }
        self.stats_lock = threading.Lock()
        
        # Callbacks
        self.completion_callbacks: List[Callable] = []
        
        # Start worker threads
        self._start_workers()
    
    def _start_workers(self):
        """Start worker threads for processing tasks."""
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._worker_thread,
                args=(i,),
                daemon=True,
                name=f"QueueWorker-{i}"
            )
            worker.start()
            self.workers.append(worker)
        
        self.logger.info(f"Started {self.max_workers} queue worker threads")
    
    def submit_task(self, channel: int, audio_file: str, metadata: Dict, 
                   priority: TaskPriority = TaskPriority.NORMAL) -> str:
        """
        Submit audio file for processing.
        
        Args:
            channel: Audio channel (1-5)
            audio_file: Path to audio file
            metadata: Recording metadata
            priority: Task priority
            
        Returns:
            Task ID for tracking
        """
        try:
            # Generate unique task ID
            task_id = str(uuid.uuid4())
            
            # Create processing task
            task = ProcessingTask(task_id, channel, audio_file, metadata, priority)
            
            # Add to task tracking
            with self.tasks_lock:
                self.tasks[task_id] = task
            
            # Add to queue
            self.task_queue.put(task, timeout=5.0)
            
            # Update statistics
            with self.stats_lock:
                self.stats['tasks_submitted'] += 1
            
            self.logger.info(f"Submitted task {task_id} for channel {channel}: {audio_file}")
            return task_id
            
        except queue.Full:
            self.logger.error("Processing queue is full, cannot submit new task")
            raise RuntimeError("Processing queue is full")
        except Exception as e:
            self.logger.error(f"Failed to submit task for channel {channel}: {e}")
            raise
    
    def _worker_thread(self, worker_id: int):
        """
        Worker thread for processing tasks from the queue.
        
        Args:
            worker_id: Worker thread identifier
        """
        self.logger.info(f"Started queue worker {worker_id}")
        
        while not self._stop_event.is_set():
            try:
                # Get next task from queue (with timeout to allow checking stop signal)
                try:
                    task = self.task_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Process the task
                self._process_task(worker_id, task)
                
                # Mark task as done in queue
                self.task_queue.task_done()
                
            except Exception as e:
                self.logger.error(f"Queue worker {worker_id} error: {e}")
                time.sleep(1.0)
        
        self.logger.info(f"Queue worker {worker_id} stopped")
    
    def _process_task(self, worker_id: int, task: ProcessingTask):
        """
        Process individual task through the pipeline.
        
        Args:
            worker_id: Worker thread identifier
            task: Processing task to execute
        """
        try:
            self.logger.info(f"Worker {worker_id} processing task {task.task_id} (channel {task.channel})")
            
            # Update task status
            with self.tasks_lock:
                task.status = TaskStatus.PROCESSING
                task.started_time = datetime.now()
            
            # Step 1: Speech processing
            speech_result = self._process_speech(task)
            if not speech_result:
                self._mark_task_failed(task, "Speech processing failed")
                return
            
            # Step 2: Content filtering
            filter_result = self._process_content_filter(task, speech_result)
            if not filter_result:
                self._mark_task_failed(task, "Content filtering failed")
                return
            
            # Mark task as completed
            self._mark_task_completed(task, {
                'speech_result': speech_result,
                'filter_result': filter_result
            })
            
            self.logger.info(f"Worker {worker_id} completed task {task.task_id}")
            
        except Exception as e:
            self.logger.error(f"Worker {worker_id} failed to process task {task.task_id}: {e}")
            self._mark_task_failed(task, str(e))
    
    def _process_speech(self, task: ProcessingTask) -> Optional[Dict]:
        """
        Process task through speech processor.
        
        Args:
            task: Processing task
            
        Returns:
            Speech processing results or None if failed
        """
        try:
            return self.speech_processor.process_audio_file(
                task.channel, task.audio_file, task.metadata
            )
        except Exception as e:
            self.logger.error(f"Speech processing error for task {task.task_id}: {e}")
            return None
    
    def _process_content_filter(self, task: ProcessingTask, speech_result: Dict) -> Optional[Dict]:
        """
        Process task through content filter.
        
        Args:
            task: Processing task
            speech_result: Results from speech processing
            
        Returns:
            Content filter results or None if failed
        """
        try:
            filter_result = self.content_filter.process_transcript(
                speech_result['channel'],
                speech_result['audio_file'],
                speech_result['transcript'],
                speech_result['confidence'],
                speech_result['metadata']
            )
            
            return filter_result
            
        except Exception as e:
            self.logger.error(f"Content filter error for task {task.task_id}: {e}")
            return None
    
    def _mark_task_completed(self, task: ProcessingTask, result: Dict):
        """Mark task as completed and update statistics."""
        with self.tasks_lock:
            task.status = TaskStatus.COMPLETED
            task.completed_time = datetime.now()
            task.result = result
        
        with self.stats_lock:
            self.stats['tasks_completed'] += 1
        
        # Call completion callbacks
        self._call_completion_callbacks(task)
    
    def _mark_task_failed(self, task: ProcessingTask, error_message: str):
        """Mark task as failed and update statistics."""
        with self.tasks_lock:
            task.status = TaskStatus.FAILED
            task.completed_time = datetime.now()
            task.error_message = error_message
        
        with self.stats_lock:
            self.stats['tasks_failed'] += 1
        
        self.logger.error(f"Task {task.task_id} failed: {error_message}")
    
    def _call_completion_callbacks(self, task: ProcessingTask):
        """Call registered completion callbacks."""
        for callback in self.completion_callbacks:
            try:
                callback(task)
            except Exception as e:
                self.logger.error(f"Error in completion callback: {e}")
    
    def add_completion_callback(self, callback: Callable):
        """
        Add callback for task completion.
        
        Args:
            callback: Function to call when task completes
                     Signature: callback(task: ProcessingTask)
        """
        self.completion_callbacks.append(callback)
        self.logger.info("Added completion callback")
    
    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """
        Get status of specific task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Task status dictionary or None if not found
        """
        with self.tasks_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            
            return {
                'task_id': task.task_id,
                'channel': task.channel,
                'audio_file': task.audio_file,
                'status': task.status.value,
                'priority': task.priority.value,
                'created_time': task.created_time.isoformat(),
                'started_time': task.started_time.isoformat() if task.started_time else None,
                'completed_time': task.completed_time.isoformat() if task.completed_time else None,
                'error_message': task.error_message,
                'result': task.result
            }
    
    def get_queue_status(self) -> Dict:
        """
        Get current queue status and statistics.
        
        Returns:
            Queue status dictionary
        """
        with self.tasks_lock:
            pending_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.PENDING]
            processing_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.PROCESSING]
            completed_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.COMPLETED]
            failed_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]
        
        with self.stats_lock:
            stats = self.stats.copy()
        
        return {
            'queue_size': self.task_queue.qsize(),
            'max_queue_size': self.max_queue_size,
            'workers': self.max_workers,
            'tasks': {
                'pending': len(pending_tasks),
                'processing': len(processing_tasks),
                'completed': len(completed_tasks),
                'failed': len(failed_tasks),
                'total': len(self.tasks)
            },
            'statistics': stats
        }
    
    def get_channel_tasks(self, channel: int, status: Optional[TaskStatus] = None) -> List[Dict]:
        """
        Get tasks for specific channel.
        
        Args:
            channel: Channel number (1-5)
            status: Optional status filter
            
        Returns:
            List of task dictionaries
        """
        with self.tasks_lock:
            channel_tasks = []
            for task in self.tasks.values():
                if task.channel == channel:
                    if status is None or task.status == status:
                        channel_tasks.append({
                            'task_id': task.task_id,
                            'status': task.status.value,
                            'audio_file': task.audio_file,
                            'created_time': task.created_time.isoformat(),
                            'completed_time': task.completed_time.isoformat() if task.completed_time else None
                        })
            
            # Sort by creation time (newest first)
            channel_tasks.sort(key=lambda x: x['created_time'], reverse=True)
            return channel_tasks
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel pending task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            True if task was cancelled successfully
        """
        with self.tasks_lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.completed_time = datetime.now()
                
                with self.stats_lock:
                    self.stats['tasks_cancelled'] += 1
                
                self.logger.info(f"Cancelled task {task_id}")
                return True
            else:
                self.logger.warning(f"Cannot cancel task {task_id} with status {task.status.value}")
                return False
    
    def clear_completed_tasks(self, max_age_hours: int = 24):
        """
        Clear completed tasks older than specified age.
        
        Args:
            max_age_hours: Maximum age in hours for keeping completed tasks
        """
        cutoff_time = datetime.now().timestamp() - (max_age_hours * 3600)
        
        with self.tasks_lock:
            tasks_to_remove = []
            for task_id, task in self.tasks.items():
                if (task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED] and
                    task.completed_time and task.completed_time.timestamp() < cutoff_time):
                    tasks_to_remove.append(task_id)
            
            for task_id in tasks_to_remove:
                del self.tasks[task_id]
        
        if tasks_to_remove:
            self.logger.info(f"Cleared {len(tasks_to_remove)} old completed tasks")
    
    def stop_workers(self):
        """Stop all worker threads."""
        self.logger.info("Stopping queue workers")
        
        self._stop_event.set()
        
        # Wait for workers to finish
        for worker in self.workers:
            worker.join(timeout=5.0)
        
        self.logger.info("Queue workers stopped")
    
    def cleanup(self):
        """Clean up queue resources."""
        try:
            self.stop_workers()
            
            # Clear remaining tasks
            with self.tasks_lock:
                self.tasks.clear()
            
            self.logger.info("File processing queue cleanup completed")
        except Exception as e:
            self.logger.error(f"File processing queue cleanup failed: {e}")