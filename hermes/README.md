# Hermes Agent boundary

This stack runs the official Nous Research Hermes Agent image, pinned by default
to `nousresearch/hermes-agent:v2026.9.24`. The supported gateway command exposes
its authenticated OpenAI-compatible API on container port `8642`. The host port
is bound to `127.0.0.1`; other containers use `http://hermes:8642`.

`hermes-init` copies `config.yaml` into the named `hermes_data` volume only when
the file does not already exist. This preserves Hermes state and avoids silently
overwriting changes made through Hermes tooling. To deliberately reseed it, stop
the stack, back up the volume, remove only the `hermes_data` volume, and start the
stack again.

The seeded model is the LiteLLM logical alias `free-default`. With the default
LiteLLM mock config, Hermes can boot without provider credentials. Real model
quality requires configuring LiteLLM as described in `../litellm/README.md`.

Hermes' API server is a powerful agent runtime, not merely an LLM proxy. Do not
publish port 8642 to a LAN or attach the Docker socket. Recruitment actions must
remain behind backend allowlists and human approval gates.

The integration follows the upstream Docker and API-server contracts. A tagged
image is used rather than `latest`; upgrades should be reviewed and tested before
changing `HERMES_IMAGE`.

