"""Tests for RCAN §19 INVOKE/INVOKE_RESULT message types."""
import pytest
from castor.rcan.invoke import InvokeRequest, InvokeResult, SkillRegistry


def test_invoke_request_to_message():
    req = InvokeRequest(skill="nav.go_to", params={"x": 1.0, "y": 2.0}, invoke_id="test-123")
    msg = req.to_message("rcan://localhost/test/bot/1", "rcan://localhost/test/bot/1")
    assert msg["type"] == "INVOKE"
    assert msg["payload"]["skill"] == "nav.go_to"
    assert msg["msg_id"] == "test-123"  # §19.3 — wire field is msg_id


def test_invoke_result_success():
    result = InvokeResult(invoke_id="test-123", status="success", result={"reached": True})
    msg = result.to_message("rcan://localhost/test/bot/1", "rcan://localhost/test/controller/1")
    assert msg["type"] == "INVOKE_RESULT"
    assert msg["payload"]["status"] == "success"


def test_skill_registry_register_and_invoke():
    registry = SkillRegistry()

    @registry.register("test.ping")
    def ping(params):
        return {"pong": True, "echo": params.get("msg")}

    req = InvokeRequest(skill="test.ping", params={"msg": "hello"})
    result = registry.invoke(req)
    assert result.status == "success"
    assert result.result["pong"] is True
    assert result.result["echo"] == "hello"


def test_skill_not_found():
    registry = SkillRegistry()
    req = InvokeRequest(skill="nonexistent.skill", params={})
    result = registry.invoke(req)
    assert result.status == "not_found"
    assert "nonexistent.skill" in result.error


def test_skill_error_handling():
    registry = SkillRegistry()

    @registry.register("test.failing")
    def failing(params):
        raise ValueError("Something went wrong")

    req = InvokeRequest(skill="test.failing", params={})
    result = registry.invoke(req)
    assert result.status == "error"
    assert "Something went wrong" in result.error


def test_list_skills():
    registry = SkillRegistry()
    registry.register_fn("a.skill", lambda p: {})
    registry.register_fn("b.skill", lambda p: {})
    skills = registry.list_skills()
    assert "a.skill" in skills
    assert "b.skill" in skills


def test_invoke_result_not_found():
    result = InvokeResult(invoke_id="test-456", status="not_found", error="skill not found")
    msg = result.to_message("rcan://a", "rcan://b")
    assert msg["payload"]["status"] == "not_found"
