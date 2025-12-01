import json
import logging
from spade.behaviour import State
from spade.message import Message

logger = logging.getLogger(__name__)

# State constants
RESEARCH_EXECUTION_STATE = "RESEARCH_EXECUTION_STATE"
DRAFT_REPORT_STATE = "DRAFT_REPORT_STATE"

class ResearchExecutionState(State):
    async def run(self):
        logger.info("[ResearchExecutionState] Delegating to Coordinator...")
        coordinator_jid = self.agent.coordinator_jid
        plan = self.agent.current_plan
        
        # Prompt for Coordinator
        prompt = f"""Please execute the following research plan. 
        For each topic, use the appropriate sub-agent (arxiv for academic, tavily for general/web).
        
        Plan:
        {json.dumps(plan, indent=2)}
        
        Accumulate all findings and provide a comprehensive 'Research Context' summary.
        End your response with <TASK_COMPLETE>.
        """
        
        msg = Message(to=coordinator_jid)
        msg.body = prompt
        msg.set_metadata("message_type", "llm")
        await self.send(msg)
        
        logger.info("[ResearchExecutionState] Waiting for Coordinator results (this may take time)...")

        final_response = None
        while True:
            response = await self.receive(timeout=600) # 10 minutes
            if response:
                logger.debug(f"[ResearchExecutionState] Received: {response.body[:100]}...")
                normalized_body = response.body.replace(" ", "").upper()
                if "<TASK_COMPLETE>" in normalized_body or "<TASKCOMPLETE>" in normalized_body or "<END>" in normalized_body or "<DONE>" in normalized_body:
                    final_response = response.body
                    break
                else:
                    pass
            else:
                logger.warning("[ResearchExecutionState] Timed out waiting for Coordinator.")
                break
        
        if final_response:
            self.agent.research_context = final_response
            self.set_next_state(DRAFT_REPORT_STATE)
        else:
            logger.error("[ResearchExecutionState] Failed to get results.")
            self.set_next_state(RESEARCH_EXECUTION_STATE) # Retry?

