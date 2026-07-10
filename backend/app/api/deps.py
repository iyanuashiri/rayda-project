from fastapi import Header, HTTPException
from typing import Dict, Any


def get_graph_config(thread_id: str = Header(default="session-001", description="Unique session ID for memory state")) -> Dict[str, Any]:
    """
    Dependency to construct the LangGraph state configuration.
    Extracting this here keeps the route logic clean and allows for easy injection 
    of authenticated user IDs or tokens later.
    """
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id header is required")
        
    return {"configurable": {"thread_id": thread_id}}