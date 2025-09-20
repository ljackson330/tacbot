import unittest
import time
import threading
from cogs.database import Database
import tempfile
import os


class TestPerformance(unittest.TestCase):
    """Performance and load testing"""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db = Database(self.temp_db.name)
        self.db.initialize_votes_table()

    def tearDown(self):
        self.db.close()
        os.unlink(self.temp_db.name)

    def test_bulk_operations_performance(self):
        """Test performance with bulk database operations"""
        start_time = time.time()

        # Perform 1000 operations
        for i in range(1000):
            response_id = f"perf_test_{i}"
            self.db.mark_response_processed(response_id)
            self.db.store_application_message(response_id, i, i)

        end_time = time.time()
        duration = end_time - start_time

        # Should complete within reasonable time (adjust threshold as needed)
        self.assertLess(duration, 10.0, f"Bulk operations took {duration:.2f}s")

    def test_concurrent_database_access(self):
        """Test database performance under concurrent access"""
        num_threads = 10
        operations_per_thread = 100
        results = []

        def worker(thread_id):
            thread_results = []
            for i in range(operations_per_thread):
                try:
                    response_id = f"thread_{thread_id}_op_{i}"
                    start = time.time()
                    self.db.mark_response_processed(response_id)
                    end = time.time()
                    thread_results.append(end - start)
                except Exception as e:
                    thread_results.append(-1)  # Error marker
            results.extend(thread_results)

        # Start all threads
        threads = []
        start_time = time.time()

        for i in range(num_threads):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        end_time = time.time()

        # Verify no errors occurred
        error_count = sum(1 for r in results if r < 0)
        self.assertEqual(error_count, 0, f"{error_count} operations failed")

        # Check average operation time
        valid_times = [r for r in results if r >= 0]
        avg_time = sum(valid_times) / len(valid_times)

        # Each operation should complete quickly
        self.assertLess(avg_time, 0.1, f"Average operation time: {avg_time:.3f}s")

        print(f"Concurrent test: {num_threads} threads, {operations_per_thread} ops each")
        print(f"Total time: {end_time - start_time:.2f}s")
        print(f"Average operation time: {avg_time:.3f}s")