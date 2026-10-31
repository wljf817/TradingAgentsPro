# TradingAgentsPro

A multi-agent US stock trading system driven by a code scheduler. Cases open only on notable events or due checks; a PM agent leads sub-agents to a conclusion, and each trade plan passes a Critic review, code risk checks and human approval before execution.

## Usage

```bash
uv sync
uv run tradingagentspro run
```

Design docs are in [docs/](docs/), specs in [openspec/](openspec/), deployment in [deploy/](deploy/).

## Credits

Built with [Claude Code](https://claude.com/claude-code).

## License

[GPL-3.0](LICENSE)
