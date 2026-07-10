from fastapi import APIRouter, HTTPException, Depends, status
from typing import Dict, Any
from langgraph.types import Command

from app.schemas import ChatRequest, ChatResponse, ApprovalRequest
from app.agent import fleet_copilot_agent
from app.api.deps import get_graph_config


router = APIRouter(prefix="/chats", tags=["Fleet Copilot"])


@router.post("/", response_model=ChatResponse)
async def chat_with_copilot(request: ChatRequest, config: Dict[str, Any] = Depends(get_graph_config)):
    """
    Main endpoint to interact with the Fleet Copilot.
    """
    try:
        inputs = {
            "company_id": request.company_id,
            "question": request.question
        }
        
        # Invoke the LangGraph agent with the injected config
        generator = fleet_copilot_agent.stream(inputs, config=config)
        
        for event in generator:
            # Handle Human-in-the-loop pauses
            if "__interrupt__" in event:
                interrupt_data = event["__interrupt__"][0].value
                return ChatResponse(
                    status="pending_human_in_the_loop",
                    message="An action has been proposed and requires explicit approval.",
                    requires_approval=True,
                    pending_action=interrupt_data.get("proposal", {})
                )
                
            # Handle successful completion
            if "fleet_copilot_agent" in event:
                return ChatResponse(
                    status="success",
                    message=event["fleet_copilot_agent"]["final_answer"]
                )
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve", response_model=ChatResponse)
async def handle_approval(request: ApprovalRequest, config: Dict[str, Any] = Depends(get_graph_config)):
    """
    Endpoint to resume the graph after an IT administrator approves or rejects an action.
    """
    try:
        # Resume the LangGraph execution by passing the decision string back in via Command
        generator = fleet_copilot_agent.stream(
            Command(resume=request.action_decision), 
            config=config
        )
        
        for event in generator:
            if "fleet_copilot_agent" in event:
                return ChatResponse(status="success", message=event["fleet_copilot_agent"]["final_answer"])
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))