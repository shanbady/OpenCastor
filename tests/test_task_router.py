from castor.providers.task_router import TaskCategory, TaskRouter


class TestTaskRouter:
    def test_selects_preferred_provider(self):
        r = TaskRouter()
        result = r.select(TaskCategory.CODE, ["anthropic", "deepseek", "ollama"])
        assert result == "deepseek"  # preferred for CODE

    def test_falls_back_when_preferred_unavailable(self):
        r = TaskRouter()
        result = r.select(TaskCategory.CODE, ["anthropic"])
        assert result == "anthropic"

    def test_returns_none_when_no_providers(self):
        r = TaskRouter()
        assert r.select(TaskCategory.REASONING, []) is None

    def test_safety_never_uses_cheap(self):
        r = TaskRouter()
        result = r.select(TaskCategory.SAFETY, ["ollama", "anthropic"])
        assert result == "anthropic"

    def test_string_category(self):
        r = TaskRouter()
        result = r.select("code", ["deepseek"])
        assert result == "deepseek"

    def test_unknown_category_falls_back_to_reasoning(self):
        r = TaskRouter()
        result = r.select("nonsense", ["anthropic"])
        assert result == "anthropic"

    def test_custom_routing_table(self):
        r = TaskRouter(routing_table={"sensor_poll": ["custom_provider"]})
        result = r.select(TaskCategory.SENSOR_POLL, ["custom_provider", "anthropic"])
        assert result == "custom_provider"

    def test_update(self):
        r = TaskRouter()
        r.update("vision", ["custom_vision"])
        assert r.select(TaskCategory.VISION, ["custom_vision"]) == "custom_vision"
