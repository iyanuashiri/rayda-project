import json
import os
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolCall, ToolMessage
# from langchain_openai import ChatOpenAI # Or whichever LLM you prefer
from langchain_openrouter import ChatOpenRouter
from langgraph.func import entrypoint, task
from langgraph.types import interrupt
from decouple import config

from app.tools import fleet_tools, propose_remediation_action
from app.models import AuditLog
from app.core.database import engine


os.environ["LANGSMITH_API_KEY"] = config("LANGSMITH_API_KEY")
os.environ["LANGSMITH_TRACING"] = config("LANGSMITH_TRACING")
os.environ["LANGSMITH_PROJECT"] = config("LANGSMITH_PROJECT")
os.environ["LANGSMITH_ENDPOINT"] = config("LANGSMITH_ENDPOINT")


OPENROUTER_API_KEY = config("OPENROUTER_API_KEY")

llm = ChatOpenRouter(model="openai/gpt-5.4-mini", api_key=OPENROUTER_API_KEY)

llm_with_tools = llm.bind_tools(fleet_tools)


def write_audit_log(company_id: str, tool_name: str, proposal: dict, decision: str):
    """Writes action proposals and human decisions securely to the database."""
    with Session(engine) as session:
        log_entry = AuditLog(
            timestamp=datetime.now(timezone.utc),
            company_id=company_id,
            action=tool_name,
            proposal_details=proposal,
            human_decision=decision
        )
        session.add(log_entry)
        session.commit()


# 2. Define the System Prompt enforcing traceabilty and tenant isolation
SYSTEM_PROMPT = """You are the Rayda Fleet Copilot. You assist IT administrators in managing fleet telemetry.
CRITICAL RULES:
1. Grounding: You must ONLY answer questions using the data provided by your tools. 
2. Traceability: Whenever you make a claim, you MUST cite the `collected_at` timestamp and `evidence_snapshot_id` provided by the tool.
3. Actions: If you propose a remediation action, explicitly explain the reason based on the telemetry data.
"""


@task
def call_llm(messages: list) -> AIMessage:
    """Task to call the LLM and get a response or tool call."""
    return llm_with_tools.invoke(messages)

@task
def execute_tool(tool_call: dict, company_id: str) -> str:
    """Executes the tool, enforcing the company_id for tenant isolation."""
    tool_name = tool_call["name"]
    args = tool_call["args"]
    
    # Overwrite/Inject company_id to prevent cross-tenant access completely
    args["company_id"] = company_id 
    
    # Find and execute the matching tool
    for tool in fleet_tools:
        if tool.name == tool_name:
            return tool.invoke(args)
            
    return json.dumps({"error": f"Tool {tool_name} not found."})


@entrypoint()
def fleet_copilot_agent(inputs: dict) -> dict:
    """
    The main reasoning loop using LangGraph's Functional API.
    """
    # Grab the inputs (with a fallback for LangSmith chat testing)
    company_id = inputs.get("company_id", "acme-001")
    question = inputs.get("question", inputs.get("messages", [{"content": ""}])[-1]["content"])
    
    # Inject the company_id dynamically so the LLM knows its context
    dynamic_system_prompt = f"""You are the Rayda Fleet Copilot. You assist IT administrators in managing fleet telemetry.
    
    CURRENT CONTEXT: 
    - You are operating in the tenant workspace for company_id: {company_id}
    - You MUST use this exact company_id whenever your tools require it. Do not ask the user for it.
    
    CRITICAL RULES:
    1. Grounding: You must ONLY answer questions using the data provided by your tools. Never answer from memory or make assumptions.
    2. Traceability for point-in-time data: Whenever you report a device finding, you MUST cite the `collected_at` timestamp and `evidence_snapshot_id` returned by the tool.
    3. Traceability for trend/insight data: When reporting results from trend tools (compliance drift, RAM constraints), you MUST include:
       - The `check_id` or metric being analyzed
       - The `trend` classification (e.g. drifting_worse, persistently_failing)
       - The `total_snapshots_analyzed` count
       - The `insight` explanation from the tool result
       Do NOT summarise these away — present each field explicitly.
    4. Actions: If you propose a remediation action, explicitly explain the reason based on the telemetry data.
    """
    
    messages = [
        SystemMessage(content=dynamic_system_prompt),
        HumanMessage(content=question)
    ]
    
    # The explicit reasoning loop
    while True:
        # Step 1: LLM decides what to do
        ai_message = call_llm(messages).result()
        messages.append(ai_message)
        
        # Step 2: If the LLM just replied with text, the loop is finished
        if not ai_message.tool_calls:
            break
            
        # Step 3: Handle Tool Calls
        for tool_call in ai_message.tool_calls:
            
            # --- HUMAN IN THE LOOP GUARDRAIL ---
            if tool_call["name"] == "propose_remediation_action":
                # Pause the graph and send the proposal back to the user
                approval = interrupt(
                    {
                        "status": "AWAITING_APPROVAL",
                        "proposal": tool_call["args"]
                    }
                )

                write_audit_log(
                    company_id=company_id, 
                    tool_name=tool_call["name"], 
                    proposal=tool_call["args"], 
                    decision=approval
                )
                
                if approval.lower() != "approved":
                    messages.append(ToolMessage(
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                        content=json.dumps({"error": "User rejected the action."})
                    ))
                    continue # Skip executing the action

            # Execute standard analytical tools
            tool_result = execute_tool(tool_call, company_id).result()
            
            # Append the tool result to the history as a ToolMessage
            messages.append(ToolMessage(
                tool_call_id=tool_call["id"],
                name=tool_call["name"],
                content=tool_result
            ))

    # Return the final conversational output
    return {"final_answer": messages[-1].content}