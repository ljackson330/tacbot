import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import discord
from cogs.chat_commands import ChatCommands


class TestChatCommands(unittest.TestCase):

    def setUp(self):
        """Set up test chat commands"""
        self.mock_bot = MagicMock()

        env_vars = {
            'GOOGLE_FORM_ID': 'test_form_id',
            'DISCORD_ID_ENTRY': 'entry.123456',
            'ADMIN_ROLE_ID': '999999999',
            'GUILD_ID': '123456789'
        }

        with patch.dict('os.environ', env_vars):
            self.commands = ChatCommands(self.mock_bot)

    def test_config_loading(self):
        """Test configuration loading"""
        self.assertEqual(self.commands.form_id, 'test_form_id')
        self.assertEqual(self.commands.discord_id_entry, 'entry.123456')
        self.assertEqual(self.commands.admin_role_id, 999999999)
        self.assertIsNotNone(self.commands.form_url)

    def test_is_admin_with_admin_permissions(self):
        """Test admin check with administrator permissions"""
        mock_member = MagicMock()
        mock_member.guild_permissions.administrator = True

        result = self.commands._is_admin(mock_member)
        self.assertTrue(result)

    def test_is_admin_with_admin_role(self):
        """Test admin check with admin role"""
        mock_member = MagicMock()
        mock_member.guild_permissions.administrator = False

        mock_role = MagicMock()
        mock_role.id = 999999999
        mock_member.roles = [mock_role]

        result = self.commands._is_admin(mock_member)
        self.assertTrue(result)

    def test_is_admin_without_permissions(self):
        """Test admin check without permissions"""
        mock_member = MagicMock()
        mock_member.guild_permissions.administrator = False
        mock_member.roles = []

        result = self.commands._is_admin(mock_member)
        self.assertFalse(result)
