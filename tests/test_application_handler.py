import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import time
from cogs.application_handler import ApplicationHandler


class TestApplicationHandler(unittest.TestCase):
    def setUp(self):
        """Set up test application handler"""
        self.mock_bot = MagicMock()
        self.mock_bot.get_guild = MagicMock()
        self.mock_bot.wait_until_ready = AsyncMock()

        # Mock environment variables
        env_vars = {
            "GUILD_ID": "123456789",
            "APPLICATION_CHANNEL_ID": "987654321",
            "GOOGLE_FORM_ID": "test_form_id",
            "ACCEPTANCE_THRESHOLD": "3",
            "DENIAL_THRESHOLD": "2",
            "MEMBER_ROLE_ID": "555555555",
            "GENERAL_CHANNEL_ID": "666666666",
            "DISCORD_ID_QUESTION_ID": "entry.123456",
            "APPLICATION_POLL_INTERVAL": "30",
        }

        # Patch everything needed to prevent async task creation
        with (
            patch("cogs.application_handler.Database") as mock_db,
            patch("cogs.application_handler.GoogleFormsService") as mock_google,
            patch.dict("os.environ", env_vars),
            patch("cogs.application_handler.tasks"),
        ):  # Prevent task creation

            self.mock_db = mock_db.return_value
            self.mock_google = mock_google.return_value

            # Create handler without starting tasks
            self.handler = ApplicationHandler.__new__(ApplicationHandler)
            self.handler.bot = self.mock_bot
            self.handler.db = self.mock_db
            self.handler.google_service = self.mock_google
            self.handler._vote_lock = unittest.mock.MagicMock()
            self.handler._api_call_times = []
            self.handler._max_calls_per_minute = 30
            self.handler._rate_limit_window = 60
            self.handler._recent_responses = {}
            self.handler._response_cache_ttl = 300
            self.handler.question_map = {}
            self.handler._processing_applications = set()

            # Initialize the new rate limiting attributes
            self.handler._last_rate_limit_time = 0
            self.handler._rate_limit_backoff = 60

            # Load config manually
            self.handler._load_config()

    def test_config_loading(self):
        """Test configuration loading from environment variables"""
        self.assertEqual(self.handler.guild_id, 123456789)
        self.assertEqual(self.handler.channel_id, 987654321)
        self.assertEqual(self.handler.form_id, "test_form_id")
        self.assertEqual(self.handler.acceptance_threshold, 3)
        self.assertEqual(self.handler.denial_threshold, 2)

    def test_discord_id_validation(self):
        """Test Discord ID validation"""
        # Valid IDs
        self.assertTrue(self.handler._validate_discord_id("123456789012345678"))
        self.assertTrue(self.handler._validate_discord_id("1234567890123456789"))

        # Invalid IDs
        self.assertFalse(self.handler._validate_discord_id("12345"))  # Too short
        self.assertFalse(self.handler._validate_discord_id("12345678901234567890123"))  # Too long
        self.assertFalse(self.handler._validate_discord_id("abc123456789012345"))  # Contains letters
        self.assertFalse(self.handler._validate_discord_id(""))  # Empty
        self.assertFalse(self.handler._validate_discord_id(None))  # None
        self.assertFalse(self.handler._validate_discord_id("4194303"))  # Too small (before Discord epoch)

    def test_extract_discord_id(self):
        """Test Discord ID extraction from form responses"""
        # Set up the handler's discord_id_question attribute
        self.handler.discord_id_question = "entry.123456"

        # Test data with valid Discord ID
        answers = {"entry.123456": {"textAnswers": {"answers": [{"value": "123456789012345678"}]}}}

        result = self.handler._extract_discord_id(answers)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "123456789012345678")
        self.assertEqual(result[1], "entry.123456")

    def test_extract_discord_id_not_found(self):
        """Test Discord ID extraction when no valid ID is found"""
        self.handler.discord_id_question = "entry.123456"

        # Test data without Discord ID
        answers = {"entry.999999": {"textAnswers": {"answers": [{"value": "not a discord id"}]}}}

        result = self.handler._extract_discord_id(answers)
        self.assertIsNone(result)

    def test_text_sanitization(self):
        """Test text sanitization for embed display"""
        # Test mention breaking
        text = "@everyone Hello @user"
        sanitized = self.handler._sanitize_text(text)
        self.assertIn("@\u200b", sanitized)

        # Test code block breaking
        text = "`malicious code`"
        sanitized = self.handler._sanitize_text(text)
        self.assertIn("`\u200b", sanitized)

        # Test URL sanitization
        text = "https://malicious.com"
        sanitized = self.handler._sanitize_text(text)
        self.assertIn("https[://]", sanitized)

    def test_decisive_vote_detection(self):
        """Test decisive vote detection logic"""
        # Test approval threshold reached
        vote_counts = {"approve": 3, "deny": 1}
        self.assertTrue(self.handler._is_decisive_vote(vote_counts, "approve"))

        # Test denial threshold reached
        vote_counts = {"approve": 1, "deny": 2}
        self.assertTrue(self.handler._is_decisive_vote(vote_counts, "deny"))

        # Test thresholds not reached
        vote_counts = {"approve": 2, "deny": 1}
        self.assertFalse(self.handler._is_decisive_vote(vote_counts, "approve"))
        self.assertFalse(self.handler._is_decisive_vote(vote_counts, "deny"))

    def test_rate_limiting(self):
        """Test rate limiting functionality with improved logic"""
        # Reset state for clean test
        self.handler._api_call_times = []
        self.handler._last_rate_limit_time = 0

        # Initially should not be rate limited
        self.assertFalse(self.handler._is_rate_limited())

        # Add calls up to but not exceeding the limit
        for _ in range(self.handler._max_calls_per_minute - 1):
            self.handler._record_api_call()

        # Should still not be rate limited
        self.assertFalse(self.handler._is_rate_limited())

        # Add one more call to reach the limit
        self.handler._record_api_call()

        # Now should be rate limited
        self.assertTrue(self.handler._is_rate_limited())

        # Should remain rate limited due to backoff even if we clear calls
        self.handler._api_call_times = []
        self.assertTrue(self.handler._is_rate_limited())  # Still in backoff period

    def test_rate_limiting_window_expiry(self):
        """Test that rate limiting expires after the window period"""
        # Simulate calls from more than 60 seconds ago
        old_time = time.time() - 70  # 70 seconds ago
        self.handler._api_call_times = [old_time] * self.handler._max_calls_per_minute
        self.handler._last_rate_limit_time = 0

        # Should not be rate limited since calls are outside the window
        self.assertFalse(self.handler._is_rate_limited())

    def test_rate_limiting_backoff_period(self):
        """Test that backoff period is respected"""
        # Set up a recent rate limit hit
        self.handler._last_rate_limit_time = time.time() - 30  # 30 seconds ago
        self.handler._api_call_times = []  # No current calls

        # Should still be rate limited due to backoff
        self.assertTrue(self.handler._is_rate_limited())

        # Simulate backoff period expiring
        self.handler._last_rate_limit_time = time.time() - 70  # 70 seconds ago

        # Should no longer be rate limited
        self.assertFalse(self.handler._is_rate_limited())

    @patch("time.time")
    def test_api_call_recording(self, mock_time):
        """Test API call timestamp recording"""
        mock_time.return_value = 1000.0

        # Reset state
        self.handler._api_call_times = []

        # Record a call
        self.handler._record_api_call()

        # Should have recorded the timestamp
        self.assertEqual(len(self.handler._api_call_times), 1)
        self.assertEqual(self.handler._api_call_times[0], 1000.0)

    def test_rate_limit_cleanup(self):
        """Test that old API call timestamps are cleaned up"""
        current_time = time.time()

        # Add some old calls and some recent calls
        old_calls = [current_time - 70, current_time - 80, current_time - 90]  # > 60 seconds old
        recent_calls = [current_time - 10, current_time - 20, current_time - 30]  # < 60 seconds old

        self.handler._api_call_times = old_calls + recent_calls
        self.handler._last_rate_limit_time = 0

        # Check rate limit (this should clean up old calls)
        self.handler._is_rate_limited()

        # Should only have recent calls left
        self.assertEqual(len(self.handler._api_call_times), 3)
        for call_time in self.handler._api_call_times:
            self.assertGreater(call_time, current_time - 60)
