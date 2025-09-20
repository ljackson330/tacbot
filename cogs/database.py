import sqlite3
import os
import logging
from typing import Optional, Dict, Any
import threading

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv('DATABASE_PATH')
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

    def cleanup_old_data(self, days: int = 30):
        """Clean up old processed responses (optional maintenance)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM processed_responses 
                WHERE processed_at < datetime('now', ? || ' days')
            ''', (f'-{days}',))

            conn.commit()
            logger.info(f"Cleaned up old data older than {days} days")

        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")

    # ===== NEW METHODS FOR IMPROVED APPLICATION HANDLER =====

    def initialize_applications_table(self):
        """Initialize the applications table (already exists above, but ensuring consistency)"""
        # This is already handled in _initialize_database, but keeping for API consistency
        pass

    def initialize_votes_table(self):
        """Initialize the votes table for application voting."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS votes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    response_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    vote_type TEXT NOT NULL CHECK (vote_type IN ('approve', 'deny')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(response_id, user_id)
                )
            """)
            conn.commit()
            logger.info("Votes table initialized")
        except Exception as e:
            logger.error(f"Error initializing votes table: {e}")
            raise

    def add_vote(self, response_id: str, user_id: int, vote_type: str):
        """Add a new vote for an application."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO votes (response_id, user_id, vote_type)
                VALUES (?, ?, ?)
            """, (response_id, user_id, vote_type))
            conn.commit()
        except Exception as e:
            logger.error(f"Error adding vote: {e}")
            raise

    def update_vote(self, response_id: str, user_id: int, vote_type: str):
        """Update an existing vote."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE votes 
                SET vote_type = ?, created_at = CURRENT_TIMESTAMP
                WHERE response_id = ? AND user_id = ?
            """, (vote_type, response_id, user_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating vote: {e}")
            raise

    def remove_vote(self, response_id: str, user_id: int):
        """Remove a vote from an application."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM votes 
                WHERE response_id = ? AND user_id = ?
            """, (response_id, user_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error removing vote: {e}")
            raise

    def get_user_vote(self, response_id: str, user_id: int) -> Optional[str]:
        """Get a user's current vote for an application."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT vote_type FROM votes 
                WHERE response_id = ? AND user_id = ?
            """, (response_id, user_id))
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Error getting user vote: {e}")
            return None

    def get_votes(self, response_id: str) -> list:
        """Get all votes for an application."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, vote_type, created_at FROM votes 
                WHERE response_id = ?
                ORDER BY created_at DESC
            """, (response_id,))
            return [{'user_id': row[0], 'vote_type': row[1], 'created_at': row[2]}
                    for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting votes: {e}")
            return []

    def get_vote_counts(self, response_id: str) -> dict:
        """Get vote counts for an application."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT vote_type, COUNT(*) FROM votes 
                WHERE response_id = ?
                GROUP BY vote_type
            """, (response_id,))
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting vote counts: {e}")
            return {}

    def get_application_stats(self) -> dict:
        """Get application statistics."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            stats = {}

            # Total applications
            cursor.execute("SELECT COUNT(*) FROM applications")
            stats['total'] = cursor.fetchone()[0]

            # Status breakdown - handle both 'accepted'/'denied' and 'accept'/'deny'
            cursor.execute("""
                SELECT 
                    CASE 
                        WHEN status = 'accept' THEN 'accepted'
                        WHEN status = 'deny' THEN 'denied'
                        ELSE status 
                    END as normalized_status, 
                    COUNT(*) 
                FROM applications 
                WHERE status IS NOT NULL AND status != 'pending'
                GROUP BY normalized_status
            """)
            for status, count in cursor.fetchall():
                stats[status] = count

            # Ensure accepted and denied exist even if 0
            if 'accepted' not in stats:
                stats['accepted'] = 0
            if 'denied' not in stats:
                stats['denied'] = 0

            # Pending (no status or status = 'pending')
            cursor.execute("SELECT COUNT(*) FROM applications WHERE status IS NULL OR status = 'pending'")
            stats['pending'] = cursor.fetchone()[0]

            return stats
        except Exception as e:
            logger.error(f"Error getting application stats: {e}")
            return {}

    # ===== EXISTING EVENT METHODS (keeping them as they are) =====

    def initialize_events_table(self):
        """Initialize the events table"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    event_date DATE,
                    participant_count INTEGER DEFAULT 0,
                    participant_names TEXT DEFAULT '',
                    deleted INTEGER DEFAULT 0
                )
            ''')

            conn.commit()
            logger.info("Events table initialized")
        except Exception as e:
            logger.error(f"Error initializing events table: {e}")

    def has_active_event(self) -> bool:
        """Check if there's an active event"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM events WHERE deleted = 0")
            return cursor.fetchone()[0] > 0
        except Exception as e:
            logger.error(f"Error checking active event: {e}")
            return False

    def store_event(self, event_id: int, event_date):
        """Store event in database"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO events (event_id, event_date, deleted)
                VALUES (?, ?, 0)
            ''', (event_id, event_date))
            conn.commit()
        except Exception as e:
            logger.error(f"Error storing event: {e}")

    def get_active_event(self):
        """Get active event"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM events WHERE deleted = 0 ORDER BY created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting active event: {e}")
            return None

    def get_all_active_events(self) -> list:
        """Get all active events from the database."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT event_id, event_date FROM events 
                WHERE deleted = 0
            """)
            return [{'event_id': row[0], 'event_date': row[1]}
                    for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting active events: {e}")
            return []

    def update_event_participants(self, event_id: int, count: int, users: list):
        """Update event participants"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            users_str = ','.join(users) if users else ''
            cursor.execute('''
                UPDATE events 
                SET participant_count = ?, participant_names = ?
                WHERE event_id = ?
            ''', (count, users_str, event_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating participants: {e}")

    def mark_event_deleted(self, event_id: int):
        """Mark event as deleted"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE events SET deleted = 1 WHERE event_id = ?
            ''', (event_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking event deleted: {e}")

    def get_event_stats(self) -> dict:
        """Get event statistics."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            stats = {}

            # Total events created
            cursor.execute("SELECT COUNT(*) FROM events")
            stats['total_events'] = cursor.fetchone()[0]

            # Active events
            cursor.execute("SELECT COUNT(*) FROM events WHERE deleted = 0")
            stats['active_events'] = cursor.fetchone()[0]

            # Completed events
            cursor.execute("SELECT COUNT(*) FROM events WHERE deleted = 1")
            stats['completed_events'] = cursor.fetchone()[0]

            # Average participants for completed events
            cursor.execute("""
                SELECT AVG(participant_count) FROM events 
                WHERE deleted = 1 AND participant_count IS NOT NULL
            """)
            result = cursor.fetchone()[0]
            if result:
                stats['avg_participants'] = float(result)

            return stats
        except Exception as e:
            logger.error(f"Error getting event stats: {e}")
            return {}

    def close(self):
        """Close database connection"""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()