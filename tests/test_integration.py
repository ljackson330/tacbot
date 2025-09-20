import unittest
import asyncio
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.database import Database
from cogs.application_handler import ApplicationHandler


class TestIntegration(unittest.TestCase):
    """Integration tests for bot components working together"""

    def setUp(self):
        """Set up integration test environment"""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db = Database(self.temp_db.name)

        # Initialize all tables
        self.db.initialize_applications_table()
        self.db.initialize_votes_table()
        self.db.initialize_events_table()

    def tearDown(self):
        """Clean up integration test environment"""
        self.db.close()
        os.unlink(self.temp_db.name)

    def test_full_application_workflow(self):
        """Test complete application workflow from submission to decision"""
        response_id = "integration_test_123"
        message_id = 999888777
        channel_id = 111222333
        user_id_1 = 444555666
        user_id_2 = 777888999
        user_id_3 = 123123123

        # 1. Store application
        self.db.store_application_message(response_id, message_id, channel_id)
        app_data = self.db.get_application_by_message_id(message_id)
        self.assertIsNotNone(app_data)
        self.assertEqual(app_data['status'], 'pending')

        # 2. Add votes (2 approvals, 1 denial)
        self.db.add_vote(response_id, user_id_1, 'approve')
        self.db.add_vote(response_id, user_id_2, 'approve')
        self.db.add_vote(response_id, user_id_3, 'deny')

        # 3. Check vote counts
        counts = self.db.get_vote_counts(response_id)
        self.assertEqual(counts['approve'], 2)
        self.assertEqual(counts['deny'], 1)

        # 4. Process application (simulate reaching threshold)
        self.db.set_application_status(response_id, 'accepted')

        # 5. Verify final state
        final_data = self.db.get_application_status(response_id)
        self.assertEqual(final_data['status'], 'accepted')

        # 6. Check statistics
        stats = self.db.get_application_stats()
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['accepted'], 1)
        self.assertEqual(stats['denied'], 0)
        self.assertEqual(stats['pending'], 0)

    def test_concurrent_voting(self):
        """Test concurrent voting operations"""
        response_id = "concurrent_test_456"
        message_id = 555666777
        channel_id = 888999000

        # Store application
        self.db.store_application_message(response_id, message_id, channel_id)

        # Simulate concurrent voting
        import threading
        import time

        results = []

        def vote_worker(user_id, vote_type):
            try:
                self.db.add_vote(response_id, user_id, vote_type)
                results.append(True)
            except Exception as e:
                print(f"Vote error: {e}")
                results.append(False)

        # Create multiple voting threads
        threads = []
        for i in range(5):
            user_id = 1000 + i
            vote_type = 'approve' if i % 2 == 0 else 'deny'
            thread = threading.Thread(target=vote_worker, args=(user_id, vote_type))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify all votes were recorded
        self.assertEqual(len(results), 5)
        self.assertTrue(all(results))

        # Verify vote counts
        counts = self.db.get_vote_counts(response_id)
        total_votes = sum(counts.values())
        self.assertEqual(total_votes, 5)