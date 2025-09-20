from cogs.database import Database
import pytest
import sys
import os
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def temp_database():
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()
    db = Database(temp_db.name)
    yield db
    try:
        db.close()
        os.unlink(temp_db.name)
    except BaseException:
        pass


@pytest.fixture(autouse=True)
def mock_env_vars():
    test_env = {
        "DISCORD_TOKEN": "test_token",
        "GUILD_ID": "123456789",
        "APPLICATION_CHANNEL_ID": "987654321",
        "GOOGLE_FORM_ID": "test_form_id",
        "GOOGLE_CREDENTIALS_FILE": "test_creds.json",
        "GOOGLE_TOKEN_FILE": "test_token.json",
        "MEMBER_ROLE_ID": "555555555",
        "GENERAL_CHANNEL_ID": "666666666",
        "DISCORD_ID_QUESTION_ID": "entry.123456",
        "ACCEPTANCE_THRESHOLD": "3",
        "DENIAL_THRESHOLD": "2",
        "APPLICATION_POLL_INTERVAL": "30",
        "DATABASE_PATH": ":memory:",
        "EVENT_CREATE_DAY": "0",  # Monday
        "EVENT_CREATE_HOUR": "20",  # 8 PM
        "EVENT_DELETE_DAY": "6",  # Sunday
        "EVENT_DELETE_HOUR": "0",  # Midnight
    }

    with patch.dict("os.environ", test_env):
        yield
