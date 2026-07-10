"""
Tests for the agent-level guardrails in app/agent.py.

These tests do NOT call a real LLM. The execute_tool task is tested
in isolation by patching fleet_tools to use a spy.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import COMPANY_A, COMPANY_B


class TestTenantIsolation:
    """
    execute_tool must always overwrite company_id with the session value,
    regardless of what the LLM placed in the tool call args.
    """

    def test_company_id_is_always_injected_from_session(self):
        """
        If the LLM tries to query a different company, execute_tool
        must overwrite it with the authorised company_id.
        """
        captured_args = {}

        mock_tool = MagicMock()
        mock_tool.name = "check_fleet_compliance"
        mock_tool.invoke = lambda args: captured_args.update(args) or json.dumps([])

        tool_call = {
            "name": "check_fleet_compliance",
            "args": {"company_id": COMPANY_B, "severity": "high"},  # LLM tried to query COMPANY_B
            "id": "call_001",
        }

        with patch("app.agent.fleet_tools", [mock_tool]):
            from app.agent import execute_tool
            execute_tool.func(tool_call, COMPANY_A)  # authorised for COMPANY_A

        assert captured_args["company_id"] == COMPANY_A, (
            "execute_tool must overwrite company_id with the session value"
        )

    def test_company_id_injection_cannot_be_bypassed_by_any_tool(self):
        """Verify injection happens for every tool in fleet_tools, not just one."""
        tool_names = ["check_fleet_compliance", "analyze_battery_degradation",
                      "get_low_disk_space_devices", "propose_remediation_action"]

        for tool_name in tool_names:
            captured_args = {}

            mock_tool = MagicMock()
            mock_tool.name = tool_name
            mock_tool.invoke = lambda args: captured_args.update(args) or json.dumps([])

            tool_call = {
                "name": tool_name,
                "args": {"company_id": COMPANY_B},
                "id": f"call_{tool_name}",
            }

            with patch("app.agent.fleet_tools", [mock_tool]):
                from app.agent import execute_tool
                execute_tool.func(tool_call, COMPANY_A)

            assert captured_args.get("company_id") == COMPANY_A, (
                f"Tenant isolation failed for tool: {tool_name}"
            )

    def test_unknown_tool_returns_error_not_exception(self):
        """A tool call for a non-existent tool should return an error dict, not raise."""
        tool_call = {
            "name": "nonexistent_tool",
            "args": {"company_id": COMPANY_A},
            "id": "call_bad",
        }

        with patch("app.agent.fleet_tools", []):
            from app.agent import execute_tool
            result = execute_tool.func(tool_call, COMPANY_A)

        parsed = json.loads(result)
        assert "error" in parsed


class TestProposeRemediationActionValidation:
    """
    propose_remediation_action is the gateway to all state-changing actions.
    It must reject invalid action types before the interrupt fires.
    """

    def test_invalid_action_type_returns_error_json(self):
        from app.tools import propose_remediation_action

        result = json.loads(propose_remediation_action.invoke({
            "company_id": COMPANY_A,
            "device_id": "dev-1",
            "action_type": "wipe_all_devices",
            "reason": "Testing invalid action",
        }))

        assert "error" in result

    def test_empty_reason_is_still_accepted_structurally(self):
        """
        The tool itself doesn't enforce reason content — the system prompt does.
        Verify the tool still returns a PENDING_HUMAN_APPROVAL payload.
        """
        from app.tools import propose_remediation_action

        result = json.loads(propose_remediation_action.invoke({
            "company_id": COMPANY_A,
            "device_id": "dev-1",
            "action_type": "create_upgrade_order",
            "reason": "",
        }))

        assert result["status"] == "PENDING_HUMAN_APPROVAL"
