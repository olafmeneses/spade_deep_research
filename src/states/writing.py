import json
import logging
from spade.behaviour import State
from spade.message import Message

logger = logging.getLogger(__name__)


class DraftReportState(State):
    NAME = "DRAFT_REPORT_STATE"

    async def run(self):
        logger.info("[DraftReportState] Drafting report...")
        writer_jid = self.agent.writer_jid
        context = self.agent.research_context
        
        prompt = f"""Based on the following Research Context, please write a comprehensive report.
        
        Research Context:
        {context}
        """
        
        msg = Message(to=writer_jid)
        msg.body = prompt
        msg.set_metadata("message_type", "llm")
        await self.send(msg)
        
        response = await self.receive(timeout=120)
        if response:
            self.agent.current_report = response.body
            self.set_next_state(ReviewReportState.NAME)
        else:
            logger.warning("[DraftReportState] Timeout waiting for writer.")
            self.set_next_state(DraftReportState.NAME)


class ReviewReportState(State):
    NAME = "REVIEW_REPORT_STATE"

    async def run(self):
        logger.info("[ReviewReportState] Reviewing report...")
        critic_jid = self.agent.critic_jid
        report = self.agent.current_report
        original_query = self.agent.user_query
        
        prompt = f"""Please critique the following report based on the original query.
        
        Original Query: {original_query}
        
        Report:
        {report}
        """
        
        msg = Message(to=critic_jid)
        msg.body = prompt
        msg.set_metadata("message_type", "llm")
        await self.send(msg)
        
        response = await self.receive(timeout=60)
        if response:
            try:
                # Clean JSON
                clean_body = response.body.strip()
                if clean_body.startswith("```json"):
                    clean_body = clean_body.split("```json")[1]
                if clean_body.endswith("```"):
                    clean_body = clean_body.rsplit("```", 1)[0]
                    
                feedback_json = json.loads(clean_body.strip())
                
                if feedback_json.get("status") == "SUFFICIENT":
                    logger.info("[ReviewReportState] Report approved!")
                    self.set_next_state(FinalOutputState.NAME)
                else:
                    logger.info(f"[ReviewReportState] Report insufficient. Feedback: {feedback_json.get('feedback')}")
                    missing = feedback_json.get("missing_information", [])
                    if missing:
                        # Create a remedial plan
                        new_plan = {
                            "research_goal": "Address missing information",
                            "topics": [
                                {"topic_id": f"missing_{i}", "query": topic, "source": "tavily", "description": "Gap filling"}
                                for i, topic in enumerate(missing)
                            ]
                        }
                        logger.info(f"[ReviewReportState] Generating new research tasks: {missing}")
                        self.agent.current_plan = new_plan  # Update plan for next cycle
                        # Import here to avoid circular import
                        from src.states.research import ResearchExecutionState
                        self.set_next_state(ResearchExecutionState.NAME)
                    else:
                        # If insufficient but no specific missing info, maybe just retry writing?
                        # Or force final output to avoid infinite loops if critic is picky
                        logger.warning("[ReviewReportState] No specific missing info provided, accepting report with warning.")
                        self.set_next_state(FinalOutputState.NAME)
                        
            except json.JSONDecodeError:
                logger.error("[ReviewReportState] Failed to parse critic response.")
                self.set_next_state(FinalOutputState.NAME)  # Fail open
        else:
            self.set_next_state(FinalOutputState.NAME)


class FinalOutputState(State):
    NAME = "FINAL_OUTPUT_STATE"

    async def run(self):
        print("\n" + "="*60)
        print("FINAL RESEARCH REPORT")
        print("="*60)
        print(self.agent.current_report)
        print("="*60)

