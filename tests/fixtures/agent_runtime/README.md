# Agent runtime stream fixtures

These fixtures contain only synthetic, sanitized event shapes. They intentionally omit prompts, responses, tool inputs, tool outputs other than Atrex's marker receipt, paths, session identifiers, URLs, and credentials.

Characterization status:

- Claude Code 2.1.220: message-level usage, terminal usage, and successful marker receipt placement in a `user`/`tool_result` event were verified with minimal no-GPU CLI runs; values and identifiers replaced.
- Codex CLI 0.145.0: startup event shape observed, but the local minimal run timed out before terminal usage; the terminal fixture remains contract-based and must be re-qualified when the backend is available.
- Qoder CLI: unavailable in the characterization environment; the fixture remains contract-based and must be re-qualified when installed.
- Pi 0.83.0: JSON session, finalized assistant usage, tool-result, `agent_end`, and `agent_settled` shapes were verified with minimal no-GPU CLI runs; values, prompts, paths, model names, and identifiers replaced.

Raw characterization streams are not stored in the repository.
