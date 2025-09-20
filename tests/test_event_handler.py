import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
import pytz
from cogs.event_handler import EventHandler


class TestEventHandler(unittest.TestCase):
    def setUp(self):
        """Set up test event handler"""
        self.mock_bot = MagicMock()

        env_vars = {
            "GUILD_ID": "123456789",
            "EVENT_VOICE_CHANNEL_ID": "987654321",
            "EVENT_NOTIFICATION_CHANNEL_ID": "555555555",
            "EVENT_NOTIFICATION_ROLE_ID": "666666666",
            "EVENT_TIME_HOUR": "17",
            "EVENT_TIME_MINUTE": "0",
            "CREATE_DAY": "0",  # Monday
            "CREATE_HOUR": "20",  # 8 PM
            "DELETE_DAY": "6",  # Sunday
            "DELETE_HOUR": "0",  # Midnight
            "TIMEZONE": "US/Eastern",
        }

        with (
            patch.dict("os.environ", env_vars),
            patch("cogs.event_handler.Database") as mock_db,
            patch("cogs.event_handler.tasks"),
        ):  # Prevent task creation

            self.mock_db = mock_db.return_value

            # Create handler without starting tasks
            self.handler = EventHandler.__new__(EventHandler)
            self.handler.bot = self.mock_bot
            self.handler.db = self.mock_db
            self.handler._last_create_check = None
            self.handler._last_delete_check = None

            # Load config manually
            self.handler._load_config()
            self.handler._validate_config()

    def test_config_loading(self):
        """Test configuration loading"""
        self.assertEqual(self.handler.guild_id, 123456789)
        self.assertEqual(self.handler.event_voice_channel_id, 987654321)
        self.assertEqual(self.handler.event_time_hour, 17)
        self.assertEqual(self.handler.create_day, 0)  # Monday
        self.assertEqual(self.handler.delete_day, 6)  # Sunday
        self.assertEqual(self.handler.timezone.zone, "US/Eastern")

    def test_config_validation(self):
        """Test configuration validation"""
        # Test valid configuration doesn't raise
        try:
            self.handler._validate_config()
        except ValueError:
            self.fail("Valid configuration should not raise ValueError")

    def test_day_name_conversion(self):
        """Test day number to name conversion"""
        self.assertEqual(self.handler._day_name(0), "Monday")
        self.assertEqual(self.handler._day_name(6), "Sunday")
        self.assertEqual(self.handler._day_name(7), "Unknown")

    def test_should_create_event(self):
        """Test event creation timing logic"""
        # Mock current time as Monday 8 PM
        monday_8pm = datetime(2024, 1, 1, 20, 0, 0)  # Monday
        monday_8pm = pytz.timezone("US/Eastern").localize(monday_8pm)

        # Should create event on Monday at 8 PM
        result = self.handler._should_create_event(monday_8pm, "2024-01-01-20")
        self.assertTrue(result)

        # Should not create on Tuesday
        tuesday_8pm = datetime(2024, 1, 2, 20, 0, 0)  # Tuesday
        tuesday_8pm = pytz.timezone("US/Eastern").localize(tuesday_8pm)
        result = self.handler._should_create_event(tuesday_8pm, "2024-01-02-20")
        self.assertFalse(result)
