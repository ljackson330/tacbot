import unittest
from unittest.mock import patch, Mock, mock_open
from cogs.google_forms_service import GoogleFormsService


class TestGoogleFormsService(unittest.TestCase):

    @patch("cogs.google_forms_service.build")
    @patch("cogs.google_forms_service.Credentials.from_authorized_user_file")
    @patch("cogs.google_forms_service.os.path.exists")
    def test_service_initialization_with_valid_token(self, mock_exists, mock_from_file, mock_build):
        """Test service initialization with valid existing token"""
        # Setup mocks
        mock_exists.return_value = True
        mock_creds = Mock()
        mock_creds.valid = True
        mock_from_file.return_value = mock_creds

        env_vars = {
            "GOOGLE_CREDENTIALS_FILE": "credentials.json",
            "GOOGLE_TOKEN_FILE": "token.json",
        }

        with patch.dict("os.environ", env_vars):
            service = GoogleFormsService()

        mock_build.assert_called_once()
        self.assertIsNotNone(service.service)

    @patch("cogs.google_forms_service.build")
    @patch("cogs.google_forms_service.InstalledAppFlow.from_client_secrets_file")
    @patch("cogs.google_forms_service.os.path.exists")
    @patch("builtins.open", mock_open())
    @patch("cogs.google_forms_service.os.chmod")
    def test_service_initialization_without_token(self, mock_chmod, mock_exists, mock_flow_class, mock_build):
        """Test service initialization when no token exists"""
        # Setup mocks
        mock_exists.return_value = False
        mock_flow = Mock()
        mock_creds = Mock()
        mock_creds.to_json.return_value = '{"token": "test"}'
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_class.return_value = mock_flow

        env_vars = {
            "GOOGLE_CREDENTIALS_FILE": "credentials.json",
            "GOOGLE_TOKEN_FILE": "token.json",
        }

        with patch.dict("os.environ", env_vars):
            service = GoogleFormsService()

        mock_flow.run_local_server.assert_called_once()
        mock_build.assert_called_once()

    def test_build_question_map(self):
        """Test building question map from form info"""
        # Create service without initialization
        service = GoogleFormsService.__new__(GoogleFormsService)

        form_info = {
            "items": [
                {
                    "title": "What is your name?",
                    "questionItem": {"question": {"questionId": "question1"}},
                },
                {
                    "title": "What is your email?",
                    "questionItem": {"question": {"questionId": "question2"}},
                },
            ]
        }

        question_map = service.build_question_map(form_info)

        self.assertEqual(len(question_map), 2)
        self.assertEqual(question_map["question1"], "What is your name?")
        self.assertEqual(question_map["question2"], "What is your email?")
