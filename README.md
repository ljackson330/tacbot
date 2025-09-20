# TacBot - Havoc Tactical Group Discord Bot

A Discord bot for managing community applications and events.

## Features

- **Application Processing**: Automated Google Forms integration for member applications
- **Voting System**: Staff voting on applications with configurable thresholds
- **Member Management**: Automatic role assignment and member onboarding
- **Event Management**: Automatic weekly event creation and management

## Installation

### Prerequisites

- Python 3.13 or higher
- Discord Bot Token
- Google Forms API credentials

### Setup

1. **Clone the repository**:
   ```bash
   git clone github.com/ljackson330/tacbot
   cd tacbot
   ```

2. **Install dependencies**:
   ```bash
   pip install -e .[dev]
   ```

3. **Configure environment variables** - Create a `.env` file:
   ```env
   # Discord
   DISCORD_TOKEN=your_discord_bot_token
   GUILD_ID=your_guild_id
   APPLICATION_CHANNEL_ID=channel_for_applications
   GENERAL_CHANNEL_ID=general_chat_channel
   MEMBER_ROLE_ID=role_to_assign_accepted_members
   ADMIN_ROLE_ID=admin_role_id

   # Google Forms
   GOOGLE_FORM_ID=your_google_form_id
   GOOGLE_CREDENTIALS_FILE=path/to/credentials.json
   GOOGLE_TOKEN_FILE=path/to/token.json
   # ID of the form question that is autopopulated with the user's UID
   DISCORD_ID_ENTRY=entry.123456

   # Application Processing
   ACCEPTANCE_THRESHOLD=3
   DENIAL_THRESHOLD=2
   APPLICATION_POLL_INTERVAL=30

   # Event Management
   EVENT_VOICE_CHANNEL_ID=voice_channel_for_events
   EVENT_NOTIFICATION_CHANNEL_ID=notification_channel
   EVENT_NOTIFICATION_ROLE_ID=role_to_ping_for_events
   EVENT_TIME_HOUR=17 # 3 PM
   EVENT_TIME_MINUTE=0 # On the hour
   EVENT_CREATE_DAY=0 # Monday
   EVENT_CREATE_HOUR=20 # 5 PM
   EVENT_DELETE_DAY=6 # Sunday
   EVENT_DELETE_HOUR=0 # 11 PM
   TIMEZONE=US/Eastern

   # Database
   DATABASE_PATH=tacbot.db
   ```

4. **Set up Google Forms API**:
   - Create a Google Cloud Project
   - Enable the Forms API
   - Download credentials JSON file
   - Run the bot once to authenticate and generate token file

## Usage

### Running the Bot

```bash
python3 bot.py
```

### Available Commands

- `/apply` - Get application form link with pre-filled Discord ID
- `/event_create` - Manually create weekly event (admin only)
- `/event_delete` - Manually delete active event (admin only)
- `/event_stats` - Show event statistics (admin only)
- `/app_stats` - Show application statistics (admin only)

### Application Workflow

1. User runs `/apply` command to get personalized form link
2. User fills out Google Form with their information
3. Bot automatically posts application to configured channel with voting buttons
4. Staff members vote approve/deny on applications
5. When threshold is reached, bot processes the decision:
   - **Accepted**: User gets member role and welcome message
   - **Denied**: User is removed from server

### Event Management

The bot automatically:
- Creates weekly events on configured day/time
- Posts notifications with role pings
- Tracks participant counts
- Deletes old events and archives participation data

## Development

### Code Quality Tools

The project includes several development tools configured in `pyproject.toml`:

```bash
# Format code
black .

# Check code style
flake8 .

# Run tests
pytest

# Run all checks
./scripts/check.sh
```

### Pre-commit Hooks (Optional)

```bash
pip install pre-commit
pre-commit install
```

This will automatically run Black and Flake8 before each commit.

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=cogs --cov-report=html

# Run specific test file
pytest tests/test_database.py
```

### Project Structure

```
tacbot/
├── bot.py                 # Main bot entry point
├── cogs/                  # Bot functionality modules
│   ├── application_handler.py  # Application processing
│   ├── chat_commands.py        # Slash commands
│   ├── event_handler.py        # Event management
│   ├── database.py             # Database operations
│   └── google_forms_service.py # Google Forms API
├── tests/                 # Unit tests
├── scripts/              # Development scripts
│   └── check.sh          # Code quality checker
├── pyproject.toml        # Project configuration
└── .env                  # Environment variables
```

## Configuration Details

### Discord IDs

Find Discord IDs by enabling Developer Mode in Discord settings, then right-clicking on channels, roles, or users.

### Google Forms Setup

1. Create a Google Form for applications
2. Include a question for Discord ID (note the entry ID from the URL)
3. Set up Google Cloud Project with Forms API enabled
4. Download service account credentials

### Voting Thresholds

- `ACCEPTANCE_THRESHOLD`: Number of approve votes needed
- `DENIAL_THRESHOLD`: Number of deny votes needed
- Staff can change their votes before thresholds are reached
- Decisive votes have a 10-second undo window

### Event Scheduling

- `EVENT_CREATE_DAY`: 0=Monday, 6=Sunday
- `EVENT_DELETE_DAY`: When to clean up old events
- Times are in 24-hour format
- Timezone configurable via `TIMEZONE` setting

## Database

The bot uses SQLite with the following tables:
- `processed_responses`: Tracks processed form submissions
- `applications`: Application status and metadata
- `votes`: Staff voting records
- `events`: Event creation and participation tracking

### Logging

The bot logs to both console and `bot.log` file. Check logs for detailed error information.

## License

MIT License - see LICENSE file for details.