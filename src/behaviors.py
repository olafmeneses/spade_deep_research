"""Session-aware behaviors and utilities for distributed agents."""

import logging
from datetime import datetime
from typing import Dict, Optional

from spade.message import Message
from spade.template import Template

logger = logging.getLogger(__name__)


def create_session_metadata(session_id: str, message_type: str) -> Dict[str, str]:
    """Create metadata dict for session-aware messages."""
    return {
        "session_id": session_id,
        "message_type": message_type,
        "timestamp": datetime.now().isoformat(),
    }


def create_response_message(to_jid: str, body: str, session_id: str, 
                            message_type: str) -> Message:
    """Create a message with session metadata."""
    msg = Message(to=to_jid)
    msg.body = body
    msg.thread = session_id
    for key, value in create_session_metadata(session_id, message_type).items():
        msg.set_metadata(key, value)
    return msg


def create_session_template(session_id: Optional[str] = None,
                            message_type: Optional[str] = None) -> Template:
    """Create a SPADE template for filtering session messages."""
    template = Template()
    if session_id:
        template.set_metadata("session_id", session_id)
    if message_type:
        template.set_metadata("message_type", message_type)
    return template


def _message_session_id(msg: Message) -> Optional[str]:
    """Extract the session identifier from message metadata or thread."""
    if hasattr(msg, "get_metadata"):
        session_id = msg.get_metadata("session_id")
        if session_id:
            return session_id
    return getattr(msg, "thread", None)


def _set_session_id_on_tools(tools, session_id: str) -> int:
    """Set session_id on all session-aware tools.

    Returns:
        Number of tools updated.
    """
    if not tools or not session_id:
        return 0

    updated = 0
    for tool in tools:
        if hasattr(tool, 'set_session_id'):
            tool.set_session_id(session_id)
            updated += 1
    return updated


def _collect_tools(agent, behaviour):
    """Collect tools from agent and behaviour, deduplicated by identity."""
    collected = []
    seen = set()

    for source in (getattr(agent, 'tools', None), getattr(behaviour, 'tools', None)):
        if not source:
            continue
        for tool in source:
            tool_id = id(tool)
            if tool_id in seen:
                continue
            seen.add(tool_id)
            collected.append(tool)

    return collected


def apply_session_tracking(agent, behaviour_name: str = 'llm_behaviour') -> bool:
    """Apply session tracking to an agent's behaviour.
    
    Patches receive() to capture session_id from incoming messages
    and send() to add session_id to outgoing messages.
    Also sets session_id on session-aware tools before tool execution.
    """
    if not hasattr(agent, behaviour_name):
        logger.warning(f"[{agent.jid}] No {behaviour_name}, cannot apply session tracking")
        return False
    
    behaviour = getattr(agent, behaviour_name)
    session_map = agent._session_map
    agent_jid = str(agent.jid)
    
    original_receive = behaviour.receive
    original_send = behaviour.send
    
    async def patched_receive(timeout=None):
        msg = await original_receive(timeout=timeout)
        if msg:
            session_id = _message_session_id(msg)
            sender = str(msg.sender).split('/')[0]
            if session_id:
                session_map[sender] = session_id
                agent._current_session_id = session_id
                logger.debug(f"[{agent_jid}] Captured session {session_id[:8]} from {sender}")
                
                # Set session_id on all current session-aware tools
                tools = _collect_tools(agent, behaviour)
                updated = _set_session_id_on_tools(tools, session_id)
                logger.debug(f"[{agent_jid}] Updated session_id on {updated} tools")
        return msg
    
    async def patched_send(msg: Message):
        recipient = str(msg.to).split('/')[0]
        session_id = _message_session_id(msg) or session_map.get(recipient)
        if session_id:
            msg.set_metadata("session_id", session_id)
            if not msg.thread:
                msg.thread = session_id
            logger.debug(f"[{agent_jid}] Added session {session_id[:8]} to {recipient}")
        await original_send(msg)
    
    behaviour.receive = patched_receive
    behaviour.send = patched_send
    
    logger.info(f"[{agent_jid}] Session tracking enabled")
    return True
