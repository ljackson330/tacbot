import os
import asyncio
from typing import List, Dict, Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import logging

logger = logging.getLogger(__name__)


class GoogleFormsService:
    def __init__(self):
        self.scopes = [
            "https://www.googleapis.com/auth/forms.responses.readonly",
            "https://www.googleapis.com/auth/forms.body.readonly"
        ]
        self.credentials_file = os.getenv('GOOGLE_CREDENTIALS_FILE')
        self.token_file = os.getenv('GOOGLE_TOKEN_FILE')

        self.service = None
        self._initialize_service()

    def _initialize_service(self):
        """Initialize the Google Forms service"""
        try:
            creds = None
            if os.path.exists(self.token_file):
                creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_file, self.scopes)
                    creds = flow.run_local_server(port=0)

                with open(self.token_file, 'w') as token:
                    token.write(creds.to_json())

            self.service = build('forms', 'v1', credentials=creds)
            logger.info("Google Forms service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Google Forms service: {e}")
            raise

    async def get_form_responses(self, form_id: str) -> List[Dict[str, Any]]:
        """
        Get all responses for a given form

        Args:
            form_id: The Google Form ID

        Returns:
            List of response dictionaries
        """
        try:
            def _get_responses():
                return self.service.forms().responses().list(formId=form_id).execute()

            # Run in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _get_responses)

            return result.get('responses', [])

        except Exception as e:
            logger.error(f"Error fetching form responses: {e}")
            return []

    async def get_form_info(self, form_id: str) -> Dict[str, Any]:
        """
        Get form metadata and questions

        Args:
            form_id: The Google Form ID

        Returns:
            Form information dictionary
        """
        try:
            def _get_form_info():
                return self.service.forms().get(formId=form_id).execute()

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _get_form_info)

            return result

        except Exception as e:
            logger.error(f"Error fetching form info: {e}")
            return {}

    def build_question_map(self, form_info: Dict[str, Any]) -> Dict[str, str]:
        """
        Build a mapping of question IDs to question titles

        Args:
            form_info: Form information from get_form_info()

        Returns:
            Dictionary mapping question IDs to titles
        """
        question_map = {}

        try:
            items = form_info.get('items', [])
            for item in items:
                # Handle different item types
                if 'questionItem' in item:
                    question = item['questionItem']['question']
                    question_id = question.get('questionId')
                    title = item.get('title', f'Question {question_id}')

                    if question_id:
                        question_map[question_id] = title

        except Exception as e:
            logger.error(f"Error building question map: {e}")

        return question_map