import json
import logging
from spade.behaviour import State
from spade.message import Message
from src.config.settings import settings

logger = logging.getLogger(__name__)


class DraftPlanState(State):
    NAME = "DRAFT_PLAN_STATE"

    async def run(self):
        logger.info("[DraftPlanState] Generating research plan...")
        user_query = self.agent.user_query
        planner_jid = self.agent.planner_jid

        # Send query to Planner Agent
        msg = Message(to=planner_jid)
        msg.body = user_query
        msg.set_metadata("message_type", "llm")
        await self.send(msg)

        # Wait for response
        response = await self.receive(timeout=60)
        
        if response:
            logger.debug(f"[DraftPlanState] Received plan: {response.body}")
            try:
                # Parse JSON plan
                clean_body = response.body.strip()
                if clean_body.startswith("```json"):
                    clean_body = clean_body.split("```json")[1]
                if clean_body.endswith("```"):
                    clean_body = clean_body.rsplit("```", 1)[0]
                
                plan = json.loads(clean_body.strip())
                self.agent.current_plan = plan
                self.set_next_state(WaitForUserValidationState.NAME)
            except json.JSONDecodeError:
                logger.warning("[DraftPlanState] Failed to parse plan JSON. Retrying...")
                self.set_next_state(DraftPlanState.NAME)
        else:
            logger.warning("[DraftPlanState] Timeout waiting for planner.")
            self.set_next_state(DraftPlanState.NAME)  # Retry


class WaitForUserValidationState(State):
    NAME = "WAIT_USER_VALIDATION_STATE"

    async def run(self):
        logger.info("[WaitForUserValidationState] Waiting for user validation")
        print("\n[WaitForUserValidationState] Proposed Plan:")
        print(json.dumps(self.agent.current_plan, indent=2))
        
        choice = await self.agent.input_func("\nApprove plan? (y/n/modify): ")
        
        if choice.lower().startswith('y'):
            # Import here to avoid circular import
            from src.states.research import ResearchExecutionState
            self.set_next_state(ResearchExecutionState.NAME)
        else:
            logger.info("[WaitForUserValidationState] Requesting modification...")
            feedback = await self.agent.input_func("Enter feedback for modification: ")
            # Update the query with feedback to refine the plan
            self.agent.user_query = f"Original request: {self.agent.initial_query}\nPrevious Plan: {json.dumps(self.agent.current_plan)}\nFeedback: {feedback}\nPlease update the plan."
            self.set_next_state(DraftPlanState.NAME)

