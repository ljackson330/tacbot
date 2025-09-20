import sqlite3
import os
import logging
from typing import Optional, Dict, Any
import threading
import time

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv("DATABASE_PATH", "tacbot.db")
        self._lock = threading.RLock()  # Use RLock for better thread safety
        self._initialize_database()

    def _get_connection(self):
        """Get a thread-safe database connection with proper error handling"""
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise

    def _execute_with_retry(
        self,
        query: str,
        params: tuple = (),
        fetch_one: bool = False,
        fetch_all: bool = False,
        commit: bool = True,
    ):
        """Execute database operations with retry logic and proper error handling"""
        max_retries = 3
        for attempt in range(max_retries):
            conn = None
            try:
                with self._lock:
                    conn = self._get_connection()
                    cursor = conn.cursor()
                    cursor.execute(query, params)

                    if commit:
                        conn.commit()

                    if fetch_one:
                        return cursor.fetchone()
                    elif fetch_all:
                        return cursor.fetchall()

                    return cursor.rowcount

            except sqlite3.OperationalError as e:
                logger.warning(
                    f"Database operation failed (attempt {
                        attempt + 1}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.1 * (2**attempt))  # Exponential backoff
            except Exception as e:
                logger.error(f"Unexpected database error: {e}")
                raise
            finally:
                if conn:
                    conn.close()

    def _initialize_database(self):
        """Create database tables if they don't exist"""
        try:
            # Table for tracking processed responses
            self._execute_with_retry(
                """
                CREATE TABLE IF NOT EXISTS processed_responses (
                    response_id TEXT PRIMARY KEY,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            # Table for application messages and their status
            self._execute_with_retry(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    response_id TEXT PRIMARY KEY,
                    message_id INTEGER,
                    channel_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            logger.info("Database initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def is_response_processed(self, response_id: str) -> bool:
        """Check if a response has already been processed"""
        try:
            result = self._execute_with_retry(
                "SELECT 1 FROM processed_responses WHERE response_id = ?",
                (response_id,),
                fetch_one=True,
            )
            return result is not None

        except Exception as e:
            logger.error(f"Error checking if response is processed: {e}")
            return False

    def mark_response_processed(self, response_id: str):
        """Mark a response as processed"""
        try:
            self._execute_with_retry("INSERT OR IGNORE INTO processed_responses (response_id) VALUES (?)", (response_id,))

        except Exception as e:
            logger.error(f"Error marking response as processed: {e}")

    def store_application_message(self, response_id: str, message_id: int, channel_id: int):
        """Store information about an application message"""
        try:
            self._execute_with_retry(
                """
                INSERT OR REPLACE INTO applications
                (response_id, message_id, channel_id, status, updated_at)
                VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP)
            """,
                (response_id, message_id, channel_id),
            )

        except Exception as e:
            logger.error(f"Error storing application message: {e}")

    def get_application_by_message_id(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get application data by Discord message ID"""
        try:
            row = self._execute_with_retry("SELECT * FROM applications WHERE message_id = ?", (message_id,), fetch_one=True)
            return dict(row) if row else None

        except Exception as e:
            logger.error(f"Error getting application by message ID: {e}")
            return None

    def set_application_status(self, response_id: str, status: str):
        """Update the status of an application"""
        try:
            self._execute_with_retry(
                """
                UPDATE applications
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE response_id = ?
            """,
                (status, response_id),
            )

        except Exception as e:
            logger.error(f"Error updating application status: {e}")

    def get_application_status(self, response_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of an application"""
        try:
            row = self._execute_with_retry("SELECT * FROM applications WHERE response_id = ?", (response_id,), fetch_one=True)
            return dict(row) if row else None

        except Exception as e:
            logger.error(f"Error getting application status: {e}")
            return None

    def record_vote(self, response_id: str, user_id: int, vote: str):
        """Record a vote on an application"""
        try:
            # Remove any existing vote from this user for this application
            self._execute_with_retry(
                "DELETE FROM application_votes WHERE response_id = ? AND user_id = ?",
                (response_id, user_id),
            )

            # Insert the new vote
            self._execute_with_retry(
                """
                INSERT INTO application_votes (response_id, user_id, vote)
                VALUES (?, ?, ?)
            """,
                (response_id, user_id, vote),
            )

        except Exception as e:
            logger.error(f"Error recording vote: {e}")

    def cleanup_old_data(self, days: int = 30):
        """Clean up old processed responses (optional maintenance)"""
        try:
            self._execute_with_retry(
                """
                DELETE FROM processed_responses
                WHERE processed_at < datetime('now', ? || ' days')
            """,
                (f"-{days}",),
            )

            logger.info(f"Cleaned up old data older than {days} days")

        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")

    # ===== NEW METHODS FOR IMPROVED APPLICATION HANDLER =====

    def initialize_applications_table(self):
        """Initialize the applications table (already exists above, but keeping for API consistency)"""
        # This is already handled in _initialize_database, but keeping for API
        # consistency
        pass

    def initialize_votes_table(self):
        """Initialize the votes table for application voting."""
        try:
            self._execute_with_retry(
                """
                CREATE TABLE IF NOT EXISTS votes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    response_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    vote_type TEXT NOT NULL CHECK (vote_type IN ('approve', 'deny')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(response_id, user_id)
                )
            """
            )
            logger.info("Votes table initialized")
        except Exception as e:
            logger.error(f"Error initializing votes table: {e}")
            raise

    def add_vote(self, response_id: str, user_id: int, vote_type: str):
        """Add a new vote for an application."""
        try:
            self._execute_with_retry(
                """
                INSERT INTO votes (response_id, user_id, vote_type)
                VALUES (?, ?, ?)
            """,
                (response_id, user_id, vote_type),
            )
        except Exception as e:
            logger.error(f"Error adding vote: {e}")
            raise

    def update_vote(self, response_id: str, user_id: int, vote_type: str):
        """Update an existing vote."""
        try:
            self._execute_with_retry(
                """
                UPDATE votes
                SET vote_type = ?, created_at = CURRENT_TIMESTAMP
                WHERE response_id = ? AND user_id = ?
            """,
                (vote_type, response_id, user_id),
            )
        except Exception as e:
            logger.error(f"Error updating vote: {e}")
            raise

    def remove_vote(self, response_id: str, user_id: int):
        """Remove a vote from an application."""
        try:
            self._execute_with_retry(
                """
                DELETE FROM votes
                WHERE response_id = ? AND user_id = ?
            """,
                (response_id, user_id),
            )
        except Exception as e:
            logger.error(f"Error removing vote: {e}")
            raise

    def get_user_vote(self, response_id: str, user_id: int) -> Optional[str]:
        """Get a user's current vote for an application."""
        try:
            result = self._execute_with_retry(
                """
                SELECT vote_type FROM votes
                WHERE response_id = ? AND user_id = ?
            """,
                (response_id, user_id),
                fetch_one=True,
            )
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Error getting user vote: {e}")
            return None

    def get_votes(self, response_id: str) -> list:
        """Get all votes for an application."""
        try:
            rows = self._execute_with_retry(
                """
                SELECT user_id, vote_type, created_at FROM votes
                WHERE response_id = ?
                ORDER BY created_at DESC
            """,
                (response_id,),
                fetch_all=True,
            )
            return [{"user_id": row[0], "vote_type": row[1], "created_at": row[2]} for row in rows]
        except Exception as e:
            logger.error(f"Error getting votes: {e}")
            return []

    def get_vote_counts(self, response_id: str) -> dict:
        """Get vote counts for an application."""
        try:
            rows = self._execute_with_retry(
                """
                SELECT vote_type, COUNT(*) FROM votes
                WHERE response_id = ?
                GROUP BY vote_type
            """,
                (response_id,),
                fetch_all=True,
            )
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.error(f"Error getting vote counts: {e}")
            return {}

    def get_application_stats(self) -> dict:
        """Get application statistics with proper SQL parameterization."""
        try:
            stats = {}

            # Use parameterized queries and proper error handling
            total_result = self._execute_with_retry("SELECT COUNT(*) FROM applications", fetch_one=True)
            stats["total"] = total_result[0] if total_result else 0

            # Status breakdown with CASE normalization
            status_results = self._execute_with_retry(
                """
                SELECT
                    CASE
                        WHEN status = ? THEN ?
                        WHEN status = ? THEN ?
                        ELSE status
                    END as normalized_status,
                    COUNT(*)
                FROM applications
                WHERE status IS NOT NULL AND status != ?
                GROUP BY normalized_status
            """,
                ("accept", "accepted", "deny", "denied", "pending"),
                fetch_all=True,
            )

            for status, count in status_results:
                stats[status] = count

            # Ensure all expected keys exist
            for key in ["accepted", "denied"]:
                if key not in stats:
                    stats[key] = 0

            # Pending count
            pending_result = self._execute_with_retry(
                "SELECT COUNT(*) FROM applications WHERE status IS NULL OR status = ?",
                ("pending",),
                fetch_one=True,
            )
            stats["pending"] = pending_result[0] if pending_result else 0

            return stats

        except Exception as e:
            logger.error(f"Error getting application stats: {e}")
            return {"total": 0, "accepted": 0, "denied": 0, "pending": 0}

    # ===== EXISTING EVENT METHODS (keeping them as they are) =====

    def initialize_events_table(self):
        """Initialize the events table"""
        try:
            self._execute_with_retry(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    event_date DATE,
                    participant_count INTEGER DEFAULT 0,
                    participant_names TEXT DEFAULT '',
                    deleted INTEGER DEFAULT 0
                )
            """
            )

            logger.info("Events table initialized")
        except Exception as e:
            logger.error(f"Error initializing events table: {e}")

    def has_active_event(self) -> bool:
        """Check if there's an active event"""
        try:
            result = self._execute_with_retry("SELECT COUNT(*) FROM events WHERE deleted = 0", fetch_one=True)
            return result[0] > 0
        except Exception as e:
            logger.error(f"Error checking active event: {e}")
            return False

    def store_event(self, event_id: int, event_date):
        """Store event in database"""
        try:
            self._execute_with_retry(
                """
                INSERT INTO events (event_id, event_date, deleted)
                VALUES (?, ?, 0)
            """,
                (event_id, event_date),
            )
        except Exception as e:
            logger.error(f"Error storing event: {e}")

    def get_active_event(self):
        """Get active event"""
        try:
            row = self._execute_with_retry(
                "SELECT * FROM events WHERE deleted = 0 ORDER BY created_at DESC LIMIT 1",
                fetch_one=True,
            )
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting active event: {e}")
            return None

    def get_all_active_events(self) -> list:
        """Get all active events from the database."""
        try:
            rows = self._execute_with_retry(
                """
                SELECT event_id, event_date FROM events
                WHERE deleted = 0
            """,
                fetch_all=True,
            )
            return [{"event_id": row[0], "event_date": row[1]} for row in rows]
        except Exception as e:
            logger.error(f"Error getting active events: {e}")
            return []

    def update_event_participants(self, event_id: int, count: int, users: list):
        """Update event participants"""
        try:
            users_str = ",".join(users) if users else ""
            self._execute_with_retry(
                """
                UPDATE events
                SET participant_count = ?, participant_names = ?
                WHERE event_id = ?
            """,
                (count, users_str, event_id),
            )
        except Exception as e:
            logger.error(f"Error updating participants: {e}")

    def mark_event_deleted(self, event_id: int):
        """Mark event as deleted"""
        try:
            self._execute_with_retry(
                """
                UPDATE events SET deleted = 1 WHERE event_id = ?
            """,
                (event_id,),
            )
        except Exception as e:
            logger.error(f"Error marking event deleted: {e}")

    def get_event_stats(self) -> dict:
        """Get event statistics."""
        try:
            stats = {}

            # Total events created
            total_result = self._execute_with_retry("SELECT COUNT(*) FROM events", fetch_one=True)
            stats["total_events"] = total_result[0] if total_result else 0

            # Active events
            active_result = self._execute_with_retry("SELECT COUNT(*) FROM events WHERE deleted = 0", fetch_one=True)
            stats["active_events"] = active_result[0] if active_result else 0

            # Completed events
            completed_result = self._execute_with_retry("SELECT COUNT(*) FROM events WHERE deleted = 1", fetch_one=True)
            stats["completed_events"] = completed_result[0] if completed_result else 0

            # Average participants for completed events
            avg_result = self._execute_with_retry(
                """
                SELECT AVG(participant_count) FROM events
                WHERE deleted = 1 AND participant_count IS NOT NULL
            """,
                fetch_one=True,
            )
            if avg_result and avg_result[0]:
                stats["avg_participants"] = float(avg_result[0])

            return stats
        except Exception as e:
            logger.error(f"Error getting event stats: {e}")
            return {}

    def close(self):
        """Close database connection"""
        # With the new connection handling, there's no persistent connection to close
        # But keeping this method for API compatibility
        pass
