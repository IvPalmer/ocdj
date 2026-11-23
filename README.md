# OCDJ

DJ music acquisition in one web app: Wanted lists, track recognition, Soulseek
downloads, an Organize pipeline (clean, tag, convert) and a library view.

Django backend with a Huey worker, React/Vite frontend, PostgreSQL and
[slskd](https://github.com/slskd/slskd), all in Docker.

## Install on your own computer

Follow [docs/INSTALL.md](docs/INSTALL.md). It works on Windows and macOS with
Docker Desktop and is written so a coding agent (Claude Code, Codex) can run it
for you: open this folder in the agent and ask it to install OCDJ following
that file. [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) then explains the
flow, each tab and how to fit the settings to your workflow.

Use the `vps-deploy` branch. `main` trails it.

## Working on the code

[AGENTS.md](AGENTS.md) has the project rules and the test commands. It also
describes the maintainer's own installation (a VPS plus a Mac); those parts do
not apply to your clone.
