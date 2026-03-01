"""Tests for MQTTActionBridge in castor/channels/mqtt_channel.py (issue #296).

Covers:
- Default / custom topics
- enable() / disable() lifecycle
- _on_action_message() dispatch (instruction format, action format, publish result)
- Invalid / empty payload handling
- publish_result() JSON serialisation
- is_enabled flag
- Mock mode (HAS_PAHO=False)
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import MagicMock, patch

from castor.channels.mqtt_channel import MQTTActionBridge, MQTTChannel

# Patch target for the HAS_PAHO flag used inside mqtt_channel
_PAHO_PATH = "castor.channels.mqtt_channel.HAS_PAHO"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_channel_with_bridge(on_message=None) -> tuple[MQTTChannel, MQTTActionBridge]:
    """Create an MQTTChannel with a mock paho client attached.

    HAS_PAHO is patched to True so that enable/disable/publish_result
    exercise the real code paths (not the no-op mock fallbacks).
    """
    ch = MQTTChannel({"broker_host": "localhost"}, on_message=on_message)
    ch._client = MagicMock()
    return ch, ch._action_bridge


def _make_msg(payload: bytes, topic: str = "opencastor/action") -> MagicMock:
    """Build a minimal mock MQTT message."""
    msg = MagicMock()
    msg.payload = payload
    msg.topic = topic
    return msg


class _RunningLoop:
    """Context manager that starts an asyncio event loop in a background daemon
    thread so that ``asyncio.run_coroutine_threadsafe`` calls can resolve."""

    def __enter__(self) -> asyncio.AbstractEventLoop:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        return self._loop

    def __exit__(self, *_):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


# ── 1. Default topics ─────────────────────────────────────────────────────────


def test_action_bridge_default_topics():
    """MQTTActionBridge should use the built-in default topic strings."""
    ch = MQTTChannel({"broker_host": "localhost"})
    bridge = ch.get_action_bridge()
    assert bridge.action_topic == "opencastor/action"
    assert bridge.result_topic == "opencastor/result"


# ── 2. Custom topics from env ─────────────────────────────────────────────────


def test_action_bridge_custom_topics_from_env(monkeypatch):
    """MQTT_ACTION_TOPIC and MQTT_RESULT_TOPIC env vars should override defaults."""
    monkeypatch.setenv("MQTT_ACTION_TOPIC", "robots/actions")
    monkeypatch.setenv("MQTT_RESULT_TOPIC", "robots/results")
    ch = MQTTChannel({"broker_host": "localhost"})
    bridge = ch.get_action_bridge()
    assert bridge.action_topic == "robots/actions"
    assert bridge.result_topic == "robots/results"


# ── 3. enable() subscribes to action topic ────────────────────────────────────


def test_enable_subscribes_to_action_topic():
    """enable() must call client.subscribe(action_topic)."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()
        bridge.enable()
        ch._client.subscribe.assert_called_once_with("opencastor/action", qos=0)


# ── 4. disable() unsubscribes ─────────────────────────────────────────────────


def test_disable_unsubscribes():
    """disable() must call client.unsubscribe(action_topic)."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()
        bridge._enabled = True
        bridge.disable()
        ch._client.unsubscribe.assert_called_once_with("opencastor/action")


# ── 5. instruction format dispatches callback ─────────────────────────────────


def test_on_action_message_instruction_format():
    """{'instruction': '...'} payload should invoke the channel callback."""
    received: list[str] = []

    def cb(channel_name, chat_id, text):
        received.append(text)
        return "ok"

    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge(on_message=cb)
        bridge._enabled = True

        # The loop must be running so run_coroutine_threadsafe can resolve
        with _RunningLoop() as loop:
            ch._loop = loop
            msg = _make_msg(json.dumps({"instruction": "go forward"}).encode())
            bridge._on_action_message(None, None, msg)
            # Give the event loop time to run the dispatched coroutine
            time.sleep(0.2)

    assert received == ["go forward"]


# ── 6. action format dispatches callback ─────────────────────────────────────


def test_on_action_message_action_format():
    """{'action': {...}} payload should serialise action dict and invoke callback."""
    received: list[str] = []
    action_dict = {"type": "move", "linear": 0.5}

    def cb(channel_name, chat_id, text):
        received.append(text)
        return "done"

    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge(on_message=cb)
        bridge._enabled = True

        with _RunningLoop() as loop:
            ch._loop = loop
            msg = _make_msg(json.dumps({"action": action_dict}).encode())
            bridge._on_action_message(None, None, msg)
            time.sleep(0.2)

    assert len(received) == 1
    parsed = json.loads(received[0])
    assert parsed == action_dict


# ── 7. result is published after dispatch ─────────────────────────────────────


def test_on_action_message_publishes_result():
    """After dispatching, _on_action_message must publish a result to result_topic."""

    def cb(channel_name, chat_id, text):
        return "ack"

    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge(on_message=cb)
        bridge._enabled = True

        with _RunningLoop() as loop:
            ch._loop = loop
            msg = _make_msg(json.dumps({"instruction": "turn left"}).encode())
            bridge._on_action_message(None, None, msg)
            time.sleep(0.2)

        # At least one publish call should target the result topic
        publish_calls = ch._client.publish.call_args_list
        result_topic_calls = [c for c in publish_calls if c.args[0] == "opencastor/result"]
        assert result_topic_calls, "Expected a publish call to opencastor/result"

        # Verify the published payload is valid JSON with 'ok' key
        payload_bytes = result_topic_calls[0].args[1]
        result = json.loads(payload_bytes.decode())
        assert "ok" in result


# ── 8. Invalid JSON is silently ignored ───────────────────────────────────────


def test_on_action_message_invalid_json_no_crash():
    """A malformed JSON payload should be silently dropped without exception."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()
        bridge._enabled = True
        # No running loop needed — returns before reaching run_coroutine_threadsafe
        msg = _make_msg(b"not valid json {{{{")
        bridge._on_action_message(None, None, msg)  # must not raise
        ch._client.publish.assert_not_called()


# ── 9. Empty payload is silently ignored ──────────────────────────────────────


def test_on_action_message_empty_payload_no_crash():
    """An empty payload should be silently ignored without exception."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()
        bridge._enabled = True
        msg = _make_msg(b"")
        bridge._on_action_message(None, None, msg)  # must not raise
        ch._client.publish.assert_not_called()


# ── 10. publish_result sends JSON ─────────────────────────────────────────────


def test_publish_result_sends_json():
    """publish_result() must serialise the dict as JSON and call client.publish."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()

        result = {"ok": True, "reply": "moved", "instruction": "go"}
        bridge.publish_result(result)

        ch._client.publish.assert_called_once()
        call_args = ch._client.publish.call_args
        topic = call_args.args[0]
        payload = call_args.args[1]
        assert topic == "opencastor/result"
        decoded = json.loads(payload.decode())
        assert decoded == result


# ── 11. Bridge starts disabled ────────────────────────────────────────────────


def test_bridge_is_disabled_initially():
    """MQTTActionBridge.is_enabled should be False immediately after construction."""
    ch = MQTTChannel({"broker_host": "localhost"})
    assert ch.get_action_bridge().is_enabled is False


# ── 12. enable() sets is_enabled True ────────────────────────────────────────


def test_enable_sets_enabled_flag():
    """is_enabled should be True after enable() succeeds."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()
        bridge.enable()
        assert bridge.is_enabled is True


# ── 13. disable() sets is_enabled False ──────────────────────────────────────


def test_disable_sets_enabled_false():
    """is_enabled should be False after disable() is called."""
    with patch(_PAHO_PATH, True):
        ch, bridge = _make_channel_with_bridge()
        bridge._enabled = True
        bridge.disable()
        assert bridge.is_enabled is False


# ── 14. Mock mode — no paho, no crash ────────────────────────────────────────


def test_mock_mode_no_crash():
    """With HAS_PAHO=False, enable/disable/publish_result must not raise."""
    with patch(_PAHO_PATH, False):
        ch = MQTTChannel({"broker_host": "localhost"})
        bridge = ch.get_action_bridge()

        # enable() is a no-op; is_enabled stays False
        bridge.enable()
        assert bridge.is_enabled is False

        # disable() is a no-op; is_enabled stays False
        bridge.disable()
        assert bridge.is_enabled is False

        # publish_result() logs a warning but does not raise
        bridge.publish_result({"ok": True})  # must not raise


# ── Bonus: get_action_bridge() returns the same instance ─────────────────────


def test_get_action_bridge_returns_same_instance():
    """get_action_bridge() should always return the same MQTTActionBridge object."""
    ch = MQTTChannel({"broker_host": "localhost"})
    assert ch.get_action_bridge() is ch._action_bridge
    assert isinstance(ch.get_action_bridge(), MQTTActionBridge)
