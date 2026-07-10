from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    company_id: str = Field(..., description="The ID of the tenant workspace.")
    question: str = Field(..., description="The natural language query from the IT admin.")
    thread_id: str = Field(default="session-001", description="Unique session ID for memory and interrupts.")


class ChatResponse(BaseModel):
    status: str
    message: str
    requires_approval: bool = False
    pending_action: Optional[Dict[str, Any]] = None


class ApprovalRequest(BaseModel):
    company_id: str
    thread_id: str
    action_decision: str = Field(..., description="Must be 'approved' or 'rejected'")