import sqlite3
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import threading

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv('DATABASE_PATH', 'community_bot.db')
        self._local = threading.local()
        self._initialize_database()

    def _get_connection(self):
        """Get a thread-local database connection"""
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(self.db_path)
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    def _initialize_database(self):
        """Create database tables if they don't exist"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Table for tracking processed responses
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_responses (
                    response_id TEXT PRIMARY KEY,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Table for application messages and their status
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS applications (
                    response_id TEXT PRIMARY KEY,
                    message_id INTEGER,
                    channel_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Table for storing application votes (optional, for analytics)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS application_votes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    response_id TEXT,
                    user_id INTEGER,
                    vote TEXT,
                    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (response_id) REFERENCES applications (response_id)
                )
            ''')

            conn.commit()
            logger.info("Database initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def is_response_processed(self, response_id: str) -> bool:
        """Check if a response has already been processed"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM processed_responses WHERE response_id = ?",
                (response_id,)
            )

            return cursor.fetchone() is not None

        except Exception as e:
            logger.error(f"Error checking if response is processed: {e}")
            return False

    def mark_response_processed(self, response_id: str):
        """Mark a response as processed"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "INSERT OR IGNORE INTO processed_responses (response_id) VALUES (?)",
                (response_id,)
            )

            conn.commit()

        except Exception as e:
            logger.error(f"Error marking response as processed: {e}")

    def store_application_message(self, response_id: str, message_id: int, channel_id: int):
        """Store information about an application message"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO applications 
                (response_id, message_id, channel_id, status, updated_at) 
                VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP)
            ''', (response_id, message_id, channel_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error storing application message: {e}")

    def get_application_by_message_id(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get application data by Discord message ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM applications WHERE message_id = ?",
                (message_id,)
            )

            row = cursor.fetchone()
            return dict(row) if row else None

        except Exception as e:
            logger.error(f"Error getting application by message ID: {e}")
            return None

    def set_application_status(self, response_id: str, status: str):
        """Update the status of an application"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE applications 
                SET status = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE response_id = ?
            ''', (status, response_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating application status: {e}")

    def get_application_status(self, response_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of an application"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM applications WHERE response_id = ?",
                (response_id,)
            )

            row = cursor.fetchone()
            return dict(row) if row else None

        except Exception as e:
            logger.error(f"Error getting application status: {e}")
            return None

    def record_vote(self, response_id: str, user_id: int, vote: str):
        """Record a vote on an application"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Remove any existing vote from this user for this application
            cursor.execute(
                "DELETE FROM application_votes WHERE response_id = ? AND user_id = ?",
                (response_id, user_id)
            )

            # Insert the new vote
            cursor.execute('''
                INSERT INTO application_votes (response_id, user_id, vote) 
                VALUES (?, ?, ?)
            ''', (response_id, user_id, vote))

            conn.commit()

        except Exception as e:
            logger.error(f"Error recording vote: {e}")

    def get_application_stats(self) -> Dict[str, int]:
        """Get statistics about applications"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT status, COUNT(*) as count 
                FROM applications 
                GROUP BY status
            ''')

            stats = {}
            for row in cursor.fetchall():
                stats[row['status']] = row['count']

            return stats

        except Exception as e:
            logger.error(f"Error getting application stats: {e}")
            return {}

    def cleanup_old_data(self, days: int = 30):
        """Clean up old processed responses (optional maintenance)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM processed_responses 
                WHERE processed_at < datetime('now', '-{} days')
            '''.format(days))

            conn.commit()
            logger.info(f"Cleaned up old data older than {days} days")

        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")

    def close(self):
        """Close database connection"""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()