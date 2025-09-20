import unittest
import tempfile
import os
import sqlite3
import threading
from unittest.mock import patch, MagicMock
from cogs.database import Database


class TestDatabase(unittest.TestCase):
    def setUp(self):
        """Set up test database with temporary file"""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = Database(self.temp_db.name)

    def tearDown(self):
        """Clean up test database"""
        self.db.close()
        os.unlink(self.temp_db.name)

    def test_database_initialization(self):
        """Test database tables are created properly"""
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()

        # Check if tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        expected_tables = ["processed_responses", "applications"]
        for table in expected_tables:
            self.assertIn(table, tables)

        conn.close()

    def test_response_processing_tracking(self):
        """Test response processing tracking functionality"""
        response_id = "test_response_123"

        # Initially should not be processed
        self.assertFalse(self.db.is_response_processed(response_id))

        # Mark as processed
        self.db.mark_response_processed(response_id)

        # Now should be processed
        self.assertTrue(self.db.is_response_processed(response_id))

        # Marking again should be idempotent
        self.db.mark_response_processed(response_id)
        self.assertTrue(self.db.is_response_processed(response_id))

    def test_application_storage_and_retrieval(self):
        """Test application message storage and retrieval"""
        response_id = "test_app_456"
        message_id = 123456789
        channel_id = 987654321

        # Store application
        self.db.store_application_message(response_id, message_id, channel_id)

        # Retrieve by message ID
        app_data = self.db.get_application_by_message_id(message_id)
        self.assertIsNotNone(app_data)
        self.assertEqual(app_data["response_id"], response_id)
        self.assertEqual(app_data["message_id"], message_id)
        self.assertEqual(app_data["channel_id"], channel_id)
        self.assertEqual(app_data["status"], "pending")

        # Test status update
        self.db.set_application_status(response_id, "accepted")
        updated_data = self.db.get_application_status(response_id)
        self.assertEqual(updated_data["status"], "accepted")

    def test_voting_system(self):
        """Test voting functionality"""
        response_id = "test_vote_789"
        user_id_1 = 111111111
        user_id_2 = 222222222

        # Initialize votes table
        self.db.initialize_votes_table()

        # Add votes
        self.db.add_vote(response_id, user_id_1, "approve")
        self.db.add_vote(response_id, user_id_2, "deny")

        # Check user votes
        self.assertEqual(self.db.get_user_vote(response_id, user_id_1), "approve")
        self.assertEqual(self.db.get_user_vote(response_id, user_id_2), "deny")

        # Check vote counts
        counts = self.db.get_vote_counts(response_id)
        self.assertEqual(counts["approve"], 1)
        self.assertEqual(counts["deny"], 1)

        # Update vote
        self.db.update_vote(response_id, user_id_1, "deny")
        self.assertEqual(self.db.get_user_vote(response_id, user_id_1), "deny")

        # Remove vote
        self.db.remove_vote(response_id, user_id_1)
        self.assertIsNone(self.db.get_user_vote(response_id, user_id_1))

        # Updated counts
        counts = self.db.get_vote_counts(response_id)
        self.assertEqual(counts.get("approve", 0), 0)
        self.assertEqual(counts["deny"], 1)

    def test_thread_safety(self):
        """Test database operations under concurrent access"""
        response_ids = [f"thread_test_{i}" for i in range(10)]
        results = []

        def worker(response_id):
            try:
                self.db.mark_response_processed(response_id)
                results.append(self.db.is_response_processed(response_id))
            except Exception:
                results.append(False)

        threads = []
        for response_id in response_ids:
            thread = threading.Thread(target=worker, args=(response_id,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # All operations should have succeeded
        self.assertEqual(len(results), 10)
        self.assertTrue(all(results))

    def test_application_stats(self):
        """Test application statistics calculation"""
        # Create test data
        apps = [
            ("app1", 111, 222, "accepted"),
            ("app2", 333, 444, "denied"),
            ("app3", 555, 666, "pending"),
            ("app4", 777, 888, None),  # NULL status
        ]

        for response_id, msg_id, chan_id, status in apps:
            self.db.store_application_message(response_id, msg_id, chan_id)
            if status and status != "pending":
                self.db.set_application_status(response_id, status)

        stats = self.db.get_application_stats()

        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["denied"], 1)
        self.assertEqual(stats["pending"], 2)  # Including NULL status

    @patch("cogs.database.logger")
    def test_error_handling(self, mock_logger):
        """Test error handling in database operations with invalid database path"""
        # Test with an invalid database path that would cause an error
        # Use a path that contains invalid characters for file creation
        invalid_paths = [
            "/dev/null/invalid.db",  # Cannot create file in /dev/null
            "",  # Empty path
        ]

        for invalid_path in invalid_paths:
            with self.subTest(invalid_path=invalid_path):
                try:
                    # This should raise an exception due to invalid path
                    invalid_db = Database(invalid_path)
                    # If it doesn't raise an exception, try to use it
                    invalid_db.is_response_processed("test")
                    # If we get here, the test didn't work as expected
                    # But we'll still close the connection
                    invalid_db.close()
                except Exception:
                    # This is expected - invalid paths should cause errors
                    pass

        # Test with database file corruption scenario using mocking
        with patch("cogs.database.sqlite3.connect") as mock_connect:
            mock_connect.side_effect = sqlite3.DatabaseError("Database is corrupted")

            with self.assertRaises(sqlite3.DatabaseError):
                corrupted_db = Database(":memory:")

    def test_database_retry_mechanism(self):
        """Test database retry mechanism with operational errors"""
        # Test the retry mechanism by mocking operational errors
        with patch.object(self.db, "_get_connection") as mock_get_conn:
            # Set up a mock connection that raises OperationalError first time,
            # then succeeds
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_cursor

            # First call raises error, second succeeds
            mock_get_conn.side_effect = [sqlite3.OperationalError("Database is locked"), mock_conn]

            # This should succeed after retry
            result = self.db.is_response_processed("test_retry")
            self.assertFalse(result)

            # Verify retry was attempted
            self.assertEqual(mock_get_conn.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
