import json

from harness.base import CLIHarness, Usage, count

# Claude Code's aliases for the latest model of each family (`claude --model`).
MODELS = [
    {"id": "fable", "name": "Fable", "default": False},
    {"id": "opus", "name": "Opus", "default": False},
    {"id": "sonnet", "name": "Sonnet", "default": False},
]


class ClaudeCodeHarness(CLIHarness):
    executable = "claude"

    def models(self):
        return [dict(model) for model in MODELS]

    def command(self, mcp_config, model=None):
        config = {"mcpServers": {"assistant": mcp_config["stdio"]}}
        chosen = ["--model", model] if model else []
        return [
            self.executable,
            "-p",
            "--output-format",
            "json",
            "--mcp-config",
            json.dumps(config),
            "--strict-mcp-config",
            "--tools",
            "",
            "--allowedTools",
            "mcp__assistant__*",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            *chosen,
        ]

    def usage(self, item):
        # The --output-format json result: Anthropic usage counts cache reads and writes
        # apart from input_tokens, so they are added back in.
        usage = item.get("usage")
        if item.get("type") != "result" or not isinstance(usage, dict):
            return None
        cached = count(usage.get("cache_read_input_tokens"))
        cost = item.get("total_cost_usd")
        return Usage(
            count(usage.get("input_tokens"))
            + count(usage.get("cache_creation_input_tokens"))
            + cached,
            cached,
            count(usage.get("output_tokens")),
            float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
        )
