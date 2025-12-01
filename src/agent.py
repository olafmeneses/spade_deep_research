from typing import Optional
import logging
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, CyclicBehaviour
from spade.message import Message
from spade.template import Template
from src.states.planning import (
    DraftPlanState, 
    WaitForUserValidationState,
    DRAFT_PLAN_STATE,
    WAIT_USER_VALIDATION_STATE
)
from src.states.research import (
    ResearchExecutionState,
    RESEARCH_EXECUTION_STATE,
    DRAFT_REPORT_STATE
)
from src.states.writing import (
    DraftReportState,
    ReviewReportState,
    FinalOutputState,
    REVIEW_REPORT_STATE,
    FINAL_OUTPUT_STATE
)

logger = logging.getLogger(__name__)

class ChatListenerBehaviour(CyclicBehaviour):
    """Listens for incoming chat messages and triggers FSM workflow"""
    
    async def run(self):
        msg = await self.receive(timeout=10)
        if msg:
            logger.info(f"[ChatListener] Received message: {msg.body}")
            query = msg.body.strip()
            
            if query:
                self.agent.user_query = query
                self.agent.initial_query = query
                self.agent.chat_sender = str(msg.sender)
                
                # Check if FSM is already running
                if self.agent.fsm is not None and self.agent.fsm.is_running:
                    logger.warning("[ChatListener] FSM already running, ignoring message from internal agent")
                else:
                    logger.info(f"[ChatListener] Starting FSM for query: {query}")
                    await self.agent.start_research_workflow()

class DeepResearchFSMBehaviour(FSMBehaviour):
    async def on_start(self):
        logger.debug(f"FSM starting at initial state {self.current_state}")

    async def on_end(self):
        logger.debug(f"FSM finished at state {self.current_state}")
        # Send final result back to chat sender if available
        if hasattr(self.agent, 'chat_sender') and self.agent.chat_sender:
            if self.agent.current_report:
                msg = Message(to=self.agent.chat_sender)
                msg.body = self.agent.current_report
                msg.set_metadata("message_type", "llm")
                await self.send(msg)
                logger.info("[FSM] Sent final report to chat sender")
        # Don't stop the agent - keep it ready for new requests

class DeepResearchAgent(Agent):
    def __init__(
        self, 
        jid: str, 
        password: str, 
        user_query: str,
        planner_jid: str,
        coordinator_jid: str,
        writer_jid: str,
        critic_jid: str,
        input_func=None,
        **kwargs
    ):
        super().__init__(jid, password, **kwargs)
        self.initial_query = user_query
        self.user_query = user_query
        self.planner_jid = planner_jid
        self.coordinator_jid = coordinator_jid
        self.writer_jid = writer_jid
        self.critic_jid = critic_jid
        self.input_func = input_func if input_func else input
        
        # Shared Data
        self.current_plan = None
        self.research_context = None
        self.current_report = None
        self.chat_sender = None
        self.fsm = None

    async def start_research_workflow(self):
        """Start a new FSM workflow for research"""
        # If there's an existing FSM, remove it safely
        if self.fsm:
            try:
                self.remove_behaviour(self.fsm)
            except ValueError:
                # Behaviour was already removed or not registered
                logger.debug("[DeepResearchAgent] FSM behaviour was already removed")
        
        # Reset state
        self.current_plan = None
        self.research_context = None
        self.current_report = None
        
        # Create and start new FSM
        self.fsm = DeepResearchFSMBehaviour()
        self._setup_fsm(self.fsm)
        self.add_behaviour(self.fsm)
        logger.info("[DeepResearchAgent] Started new FSM workflow")

    def _setup_fsm(self, fsm):
        """Configure FSM states and transitions"""
        
        # Register States
        fsm.add_state(name=DRAFT_PLAN_STATE, state=DraftPlanState(), initial=True)
        fsm.add_state(name=WAIT_USER_VALIDATION_STATE, state=WaitForUserValidationState())
        fsm.add_state(name=RESEARCH_EXECUTION_STATE, state=ResearchExecutionState())
        fsm.add_state(name=DRAFT_REPORT_STATE, state=DraftReportState())
        fsm.add_state(name=REVIEW_REPORT_STATE, state=ReviewReportState())
        fsm.add_state(name=FINAL_OUTPUT_STATE, state=FinalOutputState())
        
        # Register Transitions
        # Planning Phase
        fsm.add_transition(source=DRAFT_PLAN_STATE, dest=WAIT_USER_VALIDATION_STATE)
        fsm.add_transition(source=WAIT_USER_VALIDATION_STATE, dest=RESEARCH_EXECUTION_STATE)
        fsm.add_transition(source=WAIT_USER_VALIDATION_STATE, dest=DRAFT_PLAN_STATE) # Loop
        
        # Research Phase
        fsm.add_transition(source=RESEARCH_EXECUTION_STATE, dest=DRAFT_REPORT_STATE)
        fsm.add_transition(source=RESEARCH_EXECUTION_STATE, dest=RESEARCH_EXECUTION_STATE) # Retry loop
        
        # Writing Phase
        fsm.add_transition(source=DRAFT_REPORT_STATE, dest=REVIEW_REPORT_STATE)
        fsm.add_transition(source=DRAFT_REPORT_STATE, dest=DRAFT_REPORT_STATE) # Retry loop
        
        fsm.add_transition(source=REVIEW_REPORT_STATE, dest=FINAL_OUTPUT_STATE)
        fsm.add_transition(source=REVIEW_REPORT_STATE, dest=FINAL_OUTPUT_STATE) # Fail open path
        
        # Feedback Loop (Critic -> Research)
        fsm.add_transition(source=REVIEW_REPORT_STATE, dest=RESEARCH_EXECUTION_STATE)

    async def setup(self):
        logger.info("DeepResearchAgent starting...")
        
        # Add chat listener behaviour
        chat_listener = ChatListenerBehaviour()
        template = Template()
        template.set_metadata("message_type", "llm")
        self.add_behaviour(chat_listener, template)
        
        # If initial query provided, start FSM
        if self.initial_query:
            await self.start_research_workflow()

