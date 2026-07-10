"""
Integration tests for the FastAPI routes in app/api/routes/chats.py.

The LangGraph agent is mocked so these tests are fast and deterministic.
We test that the API layer correctly:
  - passes inputs to the agent
  - handles successful responses
  - handles Human-in-the-Loop interrupts
  - handles approval/rejection resumption
  - validates request schema
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

HEADERS = {"thread_id": "test-session-001"}
COMPANY_A = "acme-001"


def _make_stream_response(final_answer: str):
    """Simulates a normal agent completion event stream."""
    yield {"fleet_copilot_agent": {"final_answer": final_answer}}


def _make_interrupt_stream(proposal: dict):
    """Simulates an agent stream that pauses for human approval."""
    mock_interrupt = MagicMock()
    mock_interrupt.value = {"status": "AWAITING_APPROVAL", "proposal": proposal}
    yield {"__interrupt__": [mock_interrupt]}


class TestChatEndpoint:

    def test_successful_response(self):
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_stream_response("No devices are low on disk space.")

            response = client.post(
                "/api/v1/chats/",
                json={"company_id": COMPANY_A, "question": "Are any devices low on disk space?"},
                headers=HEADERS,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "No devices" in body["message"]
        assert body["requires_approval"] is False

    def test_human_in_the_loop_interrupt(self):
        proposal = {
            "company_id": COMPANY_A,
            "device_id": "dev-1",
            "action": "create_upgrade_order",
            "justification": "Battery cycle count 620 exceeds threshold.",
        }

        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_interrupt_stream(proposal)

            response = client.post(
                "/api/v1/chats/",
                json={"company_id": COMPANY_A, "question": "Flag device dev-1 for a battery upgrade."},
                headers=HEADERS,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_human_in_the_loop"
        assert body["requires_approval"] is True
        assert body["pending_action"]["action"] == "create_upgrade_order"

    def test_missing_company_id_returns_422(self):
        response = client.post(
            "/api/v1/chats/",
            json={"question": "Some question"},  # missing company_id
            headers=HEADERS,
        )
        assert response.status_code == 422

    def test_missing_question_returns_422(self):
        response = client.post(
            "/api/v1/chats/",
            json={"company_id": COMPANY_A},  # missing question
            headers=HEADERS,
        )
        assert response.status_code == 422

    def test_agent_exception_returns_500(self):
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.side_effect = RuntimeError("LLM timeout")

            response = client.post(
                "/api/v1/chats/",
                json={"company_id": COMPANY_A, "question": "Any question"},
                headers=HEADERS,
            )

        assert response.status_code == 500

    def test_thread_id_is_passed_to_agent_config(self):
        """The thread_id header must be forwarded to LangGraph as the configurable thread_id.
        FastAPI converts Header param 'thread_id' to header name 'thread-id' (underscore → hyphen).
        """
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_stream_response("OK")

            client.post(
                "/api/v1/chats/",
                json={"company_id": COMPANY_A, "question": "Test"},
                headers={"thread-id": "my-custom-thread"},  # FastAPI uses hyphen form
            )

            call_kwargs = mock_agent.stream.call_args
            config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
            assert config["configurable"]["thread_id"] == "my-custom-thread"

    def test_default_thread_id_used_when_header_absent(self):
        """When no thread-id header is sent, the default 'session-001' is used."""
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_stream_response("OK")

            client.post(
                "/api/v1/chats/",
                json={"company_id": COMPANY_A, "question": "Test"},
                # no thread-id header
            )

            call_kwargs = mock_agent.stream.call_args
            config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
            assert config["configurable"]["thread_id"] == "session-001"


class TestApprovalEndpoint:

    def test_approved_decision_resumes_agent(self):
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_stream_response(
                "Upgrade order created for device dev-1."
            )

            response = client.post(
                "/api/v1/chats/approve",
                json={
                    "company_id": COMPANY_A,
                    "thread_id": "test-session-001",
                    "action_decision": "approved",
                },
                headers=HEADERS,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "Upgrade order" in body["message"]

    def test_rejected_decision_resumes_agent(self):
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_stream_response(
                "Action was rejected. No changes were made."
            )

            response = client.post(
                "/api/v1/chats/approve",
                json={
                    "company_id": COMPANY_A,
                    "thread_id": "test-session-001",
                    "action_decision": "rejected",
                },
                headers=HEADERS,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"

    def test_approval_passes_correct_command_to_agent(self):
        """Verify the Command(resume=...) is called with the decision string."""
        from langgraph.types import Command

        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.return_value = _make_stream_response("Done.")

            client.post(
                "/api/v1/chats/approve",
                json={
                    "company_id": COMPANY_A,
                    "thread_id": "test-session-001",
                    "action_decision": "approved",
                },
                headers=HEADERS,
            )

            call_args = mock_agent.stream.call_args[0]
            command = call_args[0]
            assert isinstance(command, Command)
            assert command.resume == "approved"

    def test_approval_exception_returns_500(self):
        with patch("app.api.routes.chats.fleet_copilot_agent") as mock_agent:
            mock_agent.stream.side_effect = RuntimeError("State not found")

            response = client.post(
                "/api/v1/chats/approve",
                json={
                    "company_id": COMPANY_A,
                    "thread_id": "test-session-001",
                    "action_decision": "approved",
                },
                headers=HEADERS,
            )

        assert response.status_code == 500


class TestHealthEndpoint:

    def test_health_check_returns_healthy(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
