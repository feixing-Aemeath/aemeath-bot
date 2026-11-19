"""定下来 is answered in a new Agent session, which never saw the draft id goals_propose returned
a turn earlier (its prompt carries only this turn's message id). It must still be able to confirm:
both live Agents spun until they failed or timed out on 2026-11-08 for want of that id."""

from tests.test_chat import Agent, ids, tool
from tests.test_goals import goals as goals
from tests.test_goals import say
from tests.test_goals_recheck import stage


async def test_confirm_turn_needs_no_draft_id(goals):
    service, mcp, plugin, _ = goals
    draft = await stage(service, mcp, plugin)

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        assert draft not in prompt
        result = await tool(mcp, "goals_confirm", {"arguments": {}})
        assert result["accepted"]
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "好，定下来啦。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, "定下来"))["status"] == "ready"
    assert plugin.storage.current()["status"] == "active"


async def test_goals_get_shows_the_staged_plan(goals):
    service, mcp, plugin, _ = goals
    draft = await stage(service, mcp, plugin)

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        seen = await tool(mcp, "goals_get", {"arguments": {}})
        assert seen["draft"]["id"] == draft
        assert seen["draft"]["profile"]["title"]
        result = await tool(mcp, "goals_confirm", {"arguments": {"draft": seen["draft"]["id"]}})
        assert result["accepted"]
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "好。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, "定下来"))["status"] == "ready"
    assert plugin.storage.current()["status"] == "active"
