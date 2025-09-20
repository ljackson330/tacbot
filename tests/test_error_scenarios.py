import unittest
from unittest.mock import patch, MagicMock, mock_open
from cogs.database import Database
from cogs.google_forms_service import GoogleFormsService
import tempfile
import os
import sqlite3


class TestErrorScenarios(unittest.TestCase):
    """Test error handling and edge cases"""

    def test_database_corruption_handling(self):
        """Test handling of database corruption"""
        temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        temp_db.close()

        try:
            # Create database and corrupt it
            db = Database(temp_db.name)
            db.close()

            # Write garbage to the database file
            with open(temp_db.name, 'wb') as f:
                f.write(b'corrupted_data_not_sqlite')

            # Try to use corrupted database
            with self.assertRaises(Exception):
                corrupted_db = Database(temp_db.name)
                corrupted_db.is_response_processed("test")

        finally:
            if os.path.exists(temp_db.name):
                os.unlink(temp_db.name)

    @patch('cogs.google_forms_service.build')
    @patch('cogs.google_forms_service.InstalledAppFlow.from_client_secrets_file')
    @patch('cogs.google_forms_service.Credentials.from_authorized_user_file')
    @patch('cogs.google_forms_service.os.path.exists')
    def test_missing_credentials_file(self, mock_exists, mock_from_file, mock_flow, mock_build):
        """Test handling of missing credentials file"""

        # Mock file existence to return False for token file, True for credentials
        def exists_side_effect(path):
            if 'token.json' in path:
                return False  # Token file doesn't exist
            elif 'credentials.json' in path:
                return False  # Credentials file doesn't exist
            return True

        mock_exists.side_effect = exists_side_effect

        # Mock the flow to raise FileNotFoundError when credentials file is missing
        mock_flow.side_effect = FileNotFoundError("No such file or directory: 'nonexistent.json'")

        env_vars = {
            'GOOGLE_CREDENTIALS_FILE': 'nonexistent.json',
            'GOOGLE_TOKEN_FILE': 'token.json'
        }

        with patch.dict('os.environ', env_vars):
            with self.assertRaises(FileNotFoundError):
                GoogleFormsService()

    @patch('cogs.google_forms_service.build')
    @patch('cogs.google_forms_service.Credentials.from_authorized_user_file')
    @patch('cogs.google_forms_service.os.path.exists')
    def test_invalid_credentials_format(self, mock_exists, mock_from_file, mock_build):
        """Test handling of invalid credentials format"""

        # Mock token file exists
        mock_exists.return_value = True

        # Mock invalid credentials that raise an exception
        mock_from_file.side_effect = ValueError("Invalid credentials format")

        env_vars = {
            'GOOGLE_CREDENTIALS_FILE': 'credentials.json',
            'GOOGLE_TOKEN_FILE': 'token.json'
        }

        with patch.dict('os.environ', env_vars):
            with self.assertRaises(Exception):  # Could be ValueError or any exception during initialization
                GoogleFormsService()

    @patch('cogs.google_forms_service.build')
    @patch('cogs.google_forms_service.Request')
    @patch('cogs.google_forms_service.Credentials.from_authorized_user_file')
    @patch('cogs.google_forms_service.os.path.exists')
    def test_expired_credentials_refresh_failure(self, mock_exists, mock_from_file, mock_request, mock_build):
        """Test handling of expired credentials that fail to refresh"""

        mock_exists.return_value = True

        # Mock expired credentials
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token"

        # Mock refresh to raise an exception
        mock_creds.refresh.side_effect = Exception("Failed to refresh credentials")
        mock_from_file.return_value = mock_creds

        env_vars = {
            'GOOGLE_CREDENTIALS_FILE': 'credentials.json',
            'GOOGLE_TOKEN_FILE': 'token.json'
        }

        with patch.dict('os.environ', env_vars):
            with self.assertRaises(Exception):
                GoogleFormsService()

    def test_invalid_discord_id_formats(self):
        """Test various invalid Discord ID formats"""
        from cogs.application_handler import ApplicationHandler

        # Create minimal handler for testing
        handler = ApplicationHandler.__new__(ApplicationHandler)

        # Based on the actual implementation, these should be invalid
        invalid_ids = [
            "123",  # Too short (less than 17 digits)
            "abc123456789012345",  # Contains letters
            "123 456 789 012 345",  # Contains spaces (digits get cleaned but still too short after cleaning)
            "",  # Empty string
            None,  # None value
            "12345678901234567890123456789",  # Too long (more than 20 digits)
            "4194303",  # Below Discord epoch (7 digits, also below minimum ID)
            "!@#$%^&*()",  # Special characters (no digits)
            "   ",  # Only whitespace (no digits)
            "0",  # Single zero
            "abc",  # Only letters
        ]

        for invalid_id in invalid_ids:
            with self.subTest(discord_id=invalid_id):
                self.assertFalse(handler._validate_discord_id(invalid_id))

    def test_valid_discord_id_formats(self):
        """Test valid Discord ID formats that should pass validation"""
        from cogs.application_handler import ApplicationHandler

        handler = ApplicationHandler.__new__(ApplicationHandler)

        # These should be valid based on the actual implementation
        valid_ids = [
            "123456789012345678",  # 18 digits, above minimum
            "1234567890123456789",  # 19 digits
            "12345678901234567890",  # 20 digits
            "12345678901234567",  # 17 digits (minimum length)
            "123-456-789-012-345-678",  # Has separators but 18 digits when cleaned
            "123456789012345678   ",  # Has whitespace but valid digits
        ]

        for valid_id in valid_ids:
            with self.subTest(discord_id=valid_id):
                self.assertTrue(handler._validate_discord_id(valid_id))

    def test_edge_case_discord_id_formats(self):
        """Test edge cases that might be valid or invalid depending on implementation"""
        from cogs.application_handler import ApplicationHandler

        handler = ApplicationHandler.__new__(ApplicationHandler)

        # Test integer input (should be invalid as it's not a string)
        self.assertFalse(handler._validate_discord_id(123456789012345678))

        # Test negative string number - actually becomes valid because implementation
        # strips non-digits, so "-123456789012345678" becomes "123456789012345678"
        self.assertTrue(handler._validate_discord_id("-123456789012345678"))

        # Test strings that become invalid after digit extraction
        self.assertFalse(handler._validate_discord_id("abc"))  # No digits
        self.assertFalse(handler._validate_discord_id("---"))  # No digits after cleaning
        self.assertFalse(handler._validate_discord_id("123abc"))  # Too short after cleaning (only 3 digits)

    def test_text_sanitization_edge_cases(self):
        """Test text sanitization with edge cases"""
        from cogs.application_handler import ApplicationHandler

        handler = ApplicationHandler.__new__(ApplicationHandler)

        test_cases = [
            ("@everyone", "@\u200beveryone"),
            ("`code`", "`\u200bcode`\u200b"),
            ("@user1 @user2", "@\u200buser1 @\u200buser2"),
            ("https://evil.com", "https[://]evil.com"),
            ("http://evil.com", "http[://]evil.com"),
            ("discord.gg/invite", "discord[.]gg/invite"),
            ("", ""),
            (None, "None"),  # None converted to string
            (123, "123"),  # Integer converted to string
            ("Normal text", "Normal text"),  # No change needed
            ("Multiple @everyone @here mentions", "Multiple @\u200beveryone @\u200bhere mentions"),
            ("```python\ncode\n```", "`\u200b`\u200b`\u200bpython\ncode\n`\u200b`\u200b`\u200b"),
        ]

        for input_text, expected in test_cases:
            with self.subTest(input_text=input_text):
                result = handler._sanitize_text(input_text)
                self.assertEqual(result, expected)

    def test_database_connection_failures(self):
        """Test database connection failure scenarios"""

        with patch('cogs.database.sqlite3.connect') as mock_connect:
            # Test connection failure
            mock_connect.side_effect = sqlite3.OperationalError("Unable to open database file")

            with self.assertRaises(sqlite3.OperationalError):
                db = Database(":memory:")

    def test_google_forms_api_failures(self):
        """Test Google Forms API failure scenarios"""

        # Test form responses API failure
        service = GoogleFormsService.__new__(GoogleFormsService)
        mock_service = MagicMock()
        mock_forms = MagicMock()
        mock_responses = MagicMock()
        mock_list = MagicMock()

        # Mock API to raise an exception
        mock_list.execute.side_effect = Exception("API request failed")
        mock_responses.list.return_value = mock_list
        mock_forms.responses.return_value = mock_responses
        mock_service.forms.return_value = mock_forms
        service.service = mock_service

        async def test_api_failure():
            responses = await service.get_form_responses("test_form_id")
            # Should return empty list on error
            self.assertEqual(responses, [])

        import asyncio
        asyncio.run(test_api_failure())

    def test_form_data_parsing_edge_cases(self):
        """Test edge cases in form data parsing"""
        from cogs.application_handler import ApplicationHandler

        handler = ApplicationHandler.__new__(ApplicationHandler)
        handler.discord_id_question = 'entry.123456'

        # Test various malformed answer structures
        edge_cases = [
            # Missing textAnswers - should return None
            ({'entry.123456': {}}, None),

            # Empty answers array - should return None
            ({'entry.123456': {'textAnswers': {'answers': []}}}, None),

            # Missing value field - should return None (causes KeyError which gets caught)
            ({'entry.123456': {'textAnswers': {'answers': [{}]}}}, None),

            # Non-string value - should return None (causes AttributeError on .strip())
            ({'entry.123456': {'textAnswers': {'answers': [{'value': 123456789012345678}]}}}, None),

            # Valid string Discord ID - should succeed
            ({'entry.123456': {'textAnswers': {'answers': [{'value': '123456789012345678'}]}}},
             ('123456789012345678', 'entry.123456')),

            # Multiple answers where first is invalid, second is valid - should return None
            # because the implementation processes first answer and returns on error
            ({'entry.123456': {'textAnswers': {'answers': [
                {'value': 'invalid'},
                {'value': '123456789012345678'}
            ]}}}, None),
        ]

        for i, (answers, expected_result) in enumerate(edge_cases):
            with self.subTest(case=i):
                result = handler._extract_discord_id(answers)
                if expected_result is None:
                    self.assertIsNone(result)
                else:
                    self.assertIsNotNone(result)
                    self.assertEqual(result, expected_result)

    def test_form_data_parsing_fallback_search(self):
        """Test the fallback search mechanism in form data parsing"""
        from cogs.application_handler import ApplicationHandler

        handler = ApplicationHandler.__new__(ApplicationHandler)
        handler.discord_id_question = 'entry.123456'  # This won't be found

        # Test fallback search through all answers when direct lookup fails
        answers = {
            'entry.999999': {'textAnswers': {'answers': [{'value': '123456789012345678'}]}},
            'entry.888888': {'textAnswers': {'answers': [{'value': 'not_a_discord_id'}]}},
        }

        result = handler._extract_discord_id(answers)
        # Should find the valid Discord ID in the fallback search
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '123456789012345678')
        self.assertEqual(result[1], 'entry.999999')

    def test_form_data_extraction_with_various_question_formats(self):
        """Test Discord ID extraction with various question ID formats"""
        from cogs.application_handler import ApplicationHandler

        handler = ApplicationHandler.__new__(ApplicationHandler)
        handler.discord_id_question = '123456'  # Just the number part

        # Test different formats that should match
        test_cases = [
            # Direct match with configured question ID
            {'123456': {'textAnswers': {'answers': [{'value': '123456789012345678'}]}}},
            # With entry. prefix
            {'entry.123456': {'textAnswers': {'answers': [{'value': '123456789012345678'}]}}},
        ]

        for i, answers in enumerate(test_cases):
            with self.subTest(case=i):
                result = handler._extract_discord_id(answers)
                self.assertIsNotNone(result, f"Case {i} should find a valid Discord ID")
                self.assertEqual(result[0], '123456789012345678')


if __name__ == '__main__':
    unittest.main(verbosity=2)