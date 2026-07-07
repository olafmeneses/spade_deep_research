"""Utility functions for SPADE messaging."""

from spade.message import Message


def create_research_request(to_jid: str, query: str) -> Message:
    """Create a FIPA-compliant REQUEST message for initiating research.
    
    Args:
        to_jid: JID of the orchestrator agent
        query: Research query string
        
    Returns:
        Message with performative='request' metadata
    """
    msg = Message(to=to_jid)
    msg.body = query
    msg.set_metadata("performative", "request")
    return msg
