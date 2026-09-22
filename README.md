<img src="https://raw.githubusercontent.com/browser-use/media/main/browser-harness/banner-ink.svg" alt="Browser Harness" width="100%" />

# Browser Harness ♞

Connect an LLM directly to your real browser through one editable CDP websocket. The agent writes missing helpers as it works, so the harness improves with every task.

Try browser-harness in [Browser Use Cloud](https://cloud.browser-use.com/v4?utm_campaign=browser-harness-use-in-cloud&utm_source=github) or paste the setup prompt into your coding agent.

```
  ● agent: wants to upload a file
  │
  ● agent-workspace/agent_helpers.py → helper missing
  │
  ● agent writes it                         agent_helpers.py
  │                                                       + custom helper
  ✓ file uploaded
```

**You will never use the browser again.**

## See it work

**Task:** "Open my X profile, find my latest 20 video posts, and download them."

[![Download my latest 20 X videos](docs/download-latest-20-x-videos.gif)](https://browser-use.com/showcase/videos/download-latest-20-x-videos.mp4)

## Setup prompt

Paste into Claude Code or Codex:

```text
Install or upgrade browser-harness to the latest stable version with uv using Python 3.12, register the skill from `browser-harness skill`, and connect it to my browser. Ask whether I want local browser recordings enabled; default to no and preserve my existing preference on upgrades. Follow https://github.com/browser-use/browser-harness/blob/main/install.md if setup or connection fails.
```

The agent will open `chrome://inspect/#remote-debugging`. On first setup, tick
the checkbox so the agent can connect to your browser:

<img src="docs/setup-remote-debugging.png" alt="Remote debugging setup" width="520" style="border-radius: 12px;" />

## How it works

- [`install.md`](install.md) connects the agent to your browser.
- [`SKILL.md`](SKILL.md) teaches it the browser workflow.
- [`src/browser_harness/`](src/browser_harness/) stays protected while the agent writes reusable helpers in its local workspace.

## Scale with Browser Use Cloud

Use your local browser for logged-in, personal work. When you want many browsers in parallel—with live previews, proxies, stealth, CAPTCHA solving, and more—scale with [Browser Use Cloud](https://cloud.browser-use.com/new-api-key).

## MCP server

`browser-harness-mcp` exposes the browser control helpers as MCP tools over
stdio, so any MCP client (Claude Code, Devin, Cursor, etc.) can drive the
browser without writing a second CDP layer. See [docs/MCP.md](docs/MCP.md) for
setup and client configuration.

## Daemon lifetime and monitoring

Named sessions (`BU_NAME`) persist across CLI calls; a detached daemon with
parent PID 1 is not by itself abandoned. A daemon exits after both command
inactivity and observed absence of connections exceed `BU_IDLE_EXIT_HOURS`
(default **24**, a finite positive number). Command completion timestamps are
recorded as `last-command-at` in its log. Open IPC clients protect ongoing work.
The connection check uses `lsof`; missing/failed inspection logs an error and
keeps the daemon alive. Automatic expiry therefore requires POSIX with `lsof`.

This is deliberately conservative: the daemon's own established CDP websocket
also prevents expiry. A merely unused but still connected browser is **not**
automatically closed. Disconnected daemons must accumulate a fresh connection-free
window after restart or a probe error. The check interval is one quarter of the
idle window, capped at one hour (9 seconds for a 36-second window). The default
therefore inspects connections hourly rather than spawning `lsof` every minute.

An optional POSIX watchdog follows the transition-alert pattern (high/recovered,
quiet between transitions, failed census/delivery exits nonzero, no process kills):

```sh
python -m browser_harness.watchdog --state /path/to/watchdog-state.json --threshold 32
# Add --notify /path/to/notify for a notifier accepting --severity/--source/--body.
```

Have your supervisor run it periodically and monitor its exit status and the
state file's `checked_at` heartbeat. The threshold counts **reparented** daemons,
including connected ones, to flag accumulation for investigation, not authorize
cleanup. No scheduled job is installed by the package.

Verification without touching a live browser:
`uv run --with pytest python -m pytest tests/unit tests/integration/test_idle_exit.py -q`.
The synthetic daemon acceptance uses real IPC/lsof and `BU_IDLE_EXIT_HOURS=0.01`;
it must exit and remove its endpoint within two minutes.

## Contributing

Bug fixes, documentation improvements, and agent-generated domain skills are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

[The Bitter Lesson of Agent Harnesses](https://browser-use.com/posts/bitter-lesson-agent-harnesses) · [Web Agents That Actually Learn](https://browser-use.com/posts/web-agents-that-actually-learn)
