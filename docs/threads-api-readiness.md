# Threads API readiness checklist

The current setup instructions are maintained in [THREADS_RU.md](THREADS_RU.md).
For agent-assisted installation read [AGENT_SETUP_RU.md](AGENT_SETUP_RU.md).

Start with mock mode and MANUAL. Configure a separate live database, operator
protection, stable token encryption keys, a Meta Threads app and granted scopes.
For OAuth, the starting UI and callback must use the same origin, with the exact
registered redirect URI and a matching FRONTEND_URL. For a local installation,
the UI also supports validating and importing an officially generated user token.

Verify the expected profile, live mode, granted capabilities and token validity.
`POST /api/threads/account/test` is a protected POST, not a public GET.
A connected profile does not prove live publishing: a first real post requires
the owner's decision and verification on the profile. Never reset volumes or
switch a live database to mock as an OAuth troubleshooting step.
