"""Auto-start the Signal chat bridge on agent initialization.

Only starts if:
  - Signal API and phone number are configured
  - chat_bridge.auto_start is true in config
  - At least one chat contact is registered
"""

import asyncio
import logging

logger = logging.getLogger("signal_chat_bridge")


async def execute(agent, **kwargs):
    try:
        from helpers import plugins

        config = plugins.get_plugin_config("signal", agent=agent)
        phone_number = config.get("phone_number", "")
        api_url = config.get("api", {}).get("base_url", "")

        if not phone_number or not api_url:
            return  # Not configured, skip

        bridge_config = config.get("chat_bridge", {})
        if not bridge_config.get("auto_start", False):
            return  # Auto-start disabled

        from plugins.signal.helpers.signal_bridge import get_chat_contacts, start_chat_bridge

        contacts = get_chat_contacts()
        if not contacts:
            return  # No contacts configured

        logger.info(f"Auto-starting Signal chat bridge ({len(contacts)} contact(s))...")
        await start_chat_bridge()
        logger.info("Signal chat bridge auto-started successfully.")

    except Exception as e:
        logger.warning(f"Signal chat bridge auto-start failed: {e}")
