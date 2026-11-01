"""DeepSeek's harness: the agent loop over the DeepSeek API with the assistant's tools."""

import json

import httpx

from harness.deepseek import DeepSeekHarness

CONFIG = {"environment": {"ASSISTANT_TOKEN": "core-token", "ASSISTANT_BASE_URL": "http://core"}}
TOOLS = [
    {
        "name": "todos_list",
        "description": "List todos.",
        "inputSchema": {"type": "object", "properties": {"arguments": {"type": "object"}}},
    }
]


def fake(answers, seen):
    """The core's /internal/tools and the DeepSeek API, answering in turn."""

    def handle(request):
        seen.append(request)
        if request.url.host == "core":
            assert request.headers["authorization"] == "Bearer core-token"
            if request.method == "GET":
                return httpx.Response(200, json=TOOLS)
            return httpx.Response(
                200, json={"content": [{"type": "text", "text": '{"items": []}'}]}
            )
        assert request.headers["authorization"] == "Bearer sk-test"
        if request.url.path == "/models":
            ids = ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"]
            return httpx.Response(200, json={"data": [{"id": i} for i in ids]})
        return answers.pop(0)

    return httpx.MockTransport(handle)


def completion(message, prompt=10, cached=4, completion_tokens=5):
    usage = {
        "prompt_tokens": prompt,
        "prompt_cache_hit_tokens": cached,
        "completion_tokens": completion_tokens,
    }
    return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage})


async def test_the_loop_calls_tools_until_the_model_answers():
    seen = []
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "todos_list", "arguments": '{"arguments": {}}'},
    }
    answers = [
        completion({"role": "assistant", "content": "", "tool_calls": [call]}),
        completion({"role": "assistant", "content": "已写入"}),
    ]
    harness = DeepSeekHarness(key="sk-test", transport=fake(answers, seen))
    result = await harness.run("提炼待办", CONFIG, 30)
    assert result.success and result.error is None
    assert (result.usage.input_tokens, result.usage.cached_input_tokens) == (20, 8)
    assert result.usage.output_tokens == 10
    chats = [r for r in seen if r.url.path == "/chat/completions"]
    first, second = (json.loads(r.content) for r in chats)
    # The model listed, a flash one picked; the assistant's tools offered as functions.
    assert first["model"] == "deepseek-v4-flash"
    assert first["tools"][0]["function"]["name"] == "todos_list"
    assert first["messages"] == [{"role": "user", "content": "提炼待办"}]
    assert second["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"items": []}',
    }
    tool_call = next(r for r in seen if r.url.path == "/internal/tools/todos_list")
    assert json.loads(tool_call.content) == {"arguments": {}}
    # A model picked for the run wins.
    answers.append(completion({"role": "assistant", "content": "好"}))
    await harness.run("在吗", CONFIG, 30, model="deepseek-v4-pro")
    assert json.loads(seen[-1].content)["model"] == "deepseek-v4-pro"


async def test_without_a_key_or_with_a_wrong_one_it_fails_plainly():
    assert not DeepSeekHarness().available()
    assert (await DeepSeekHarness().run("x", CONFIG, 5)).error == "deepseek_key_missing"
    seen = []
    denied = [httpx.Response(401, json={"error": {"message": "Authentication Fails"}})]
    harness = DeepSeekHarness(
        key="sk-test", model="deepseek-v4-flash", transport=fake(denied, seen)
    )
    result = await harness.run("x", CONFIG, 5)
    assert not result.success and result.error == "authentication_required"


async def test_models_are_listed_for_the_chat_box():
    seen = []
    harness = DeepSeekHarness(key="sk-test", transport=fake([], seen))
    assert harness.models() == []  # read in the background the first time
    await harness.reading
    assert harness.models() == [
        {"id": "deepseek-v4-pro", "name": "deepseek-v4-pro", "default": False},
        {"id": "deepseek-v4-flash", "name": "deepseek-v4-flash", "default": True},
        {
            "id": "deepseek-v4-flash-vision-exp",
            "name": "deepseek-v4-flash-vision-exp",
            "default": False,
        },
    ]
    assert DeepSeekHarness().models() == []  # no key: nothing to read


async def test_a_model_that_keeps_calling_tools_is_cut_off(monkeypatch):
    monkeypatch.setattr("harness.deepseek.MAX_TURNS", 3)
    seen = []
    call = {"id": "c", "type": "function", "function": {"name": "todos_list", "arguments": "{}"}}
    answers = [
        completion({"role": "assistant", "content": "", "tool_calls": [call]}) for _ in range(3)
    ]
    harness = DeepSeekHarness(
        key="sk-test", model="deepseek-v4-flash", transport=fake(answers, seen)
    )
    assert (await harness.run("x", CONFIG, 5)).error == "agent_turn_limit"
