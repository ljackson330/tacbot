import os
import json
import asyncio
from typing import List, Dict, Any
from apiclient import discovery
from httplib2 import Http
from oauth2client import client, file, tools
import logging

logger = logging.getLogger(__name__)


class GoogleFormsService:
    def __init__(self):
        self.scopes = "https://www.googleapis.com/auth/forms.responses.readonly"
        self.discovery_doc = "https://forms.googleapis.com/$discovery/rest?version=v1"
        self.credentials_file = os.getenv('GOOGLE_CREDENTIALS_FILE', 'client_secrets.json')
        self.token_file = os.getenv('GOOGLE_TOKEN_FILE', 'token.json')

        self.service = None
        self._initialize_service()

    def _initialize_service(self):
        """Initialize the Google Forms service"""
        try:
            store = file.Storage(self.token_file)
            creds = store.get()

            if not creds or creds.invalid:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(f"Google credentials file not found: {self.credentials_file}")

                flow = client.flow_from_clientsecrets(self.credentials_file, self.scopes)
                creds = tools.run_flow(flow, store)

            self.service = discovery.build(
                "forms",
                "v1",
                http=creds.authorize(Http()),
                discoveryServiceUrl=self.discovery_doc,
                static_discovery=False,
            )

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
            # Run in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.service.forms().responses().list(formId=form_id).execute()
            )

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
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.service.forms().get(formId=form_id).execute()
            )

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
                question_id = item.get('questionItem', {}).get('question', {}).get('questionId')
                title = item.get('title', f'Question {question_id}')

                if question_id:
                    question_map[question_id] = title

        except Exception as e:
            logger.error(f"Error building question map: {e}")

        return question_map