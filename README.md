# CTF Agent

## MSG CTF mode

This fork can use the participant API implemented in
[`MSG-CTF/msg-backend`](https://github.com/MSG-CTF/msg-backend) while keeping the
existing CTFd mode. It polls `GET /api/v1/board/opened_challenges`, retrieves
details and attached files for opened problems, starts a challenge instance when
the detail says one is required, runs the existing Codex solver, and submits
confirmed flags to `POST /api/v1/challenges/{id}/submit`. Only challenges already
opened by your team appear in that API; board dice rolls and challenge selection
remain manual.

```sh
cp .env.msgctf.example .env
# Edit MSGCTF_URL, MSGCTF_LOGIN_ID, MSGCTF_PASSWORD in .env.
codex login
./run-msgctf.sh --list       # check authentication and opened problems
./run-msgctf.sh --no-submit  # solve without sending flags
./run-msgctf.sh              # solve and submit during the event
```

The runner builds the solver's full `ctf-sandbox` image once, then builds and
starts the coordinator image with Docker Compose. The first sandbox build downloads
many CTF tools and can take a while. Challenge files and writeups are saved under
`data/challenges/`, and solver traces under `data/logs/`. Use `Ctrl+C` to stop the coordinator.

The Compose coordinator mounts the host Docker socket. On first run, the runner
copies only your Codex CLI `auth.json` into `data/codex-home/`; this avoids a
host Codex configuration that points at a host-only `localhost` service. The
copy is writable so Codex can refresh its login. If you log in with a different
host account later, remove `data/codex-home/auth.json` before starting again.
Child solver containers receive challenge files and a scratch workspace; they
do not receive MSG CTF credentials or the Codex login directory.
`--max-challenges 1` is intentional: the backend permits one active instance per
participant and may require confirmation before replacing it. The adapter will
confirm replacement using the current instance ID when the backend requests it.
When multiple unsolved problems are open, the runner rotates after 15 minutes on
one problem so the next problem gets a turn. Set `MSGCTF_SWARM_TIMESLICE_SECONDS`
in `.env` to change this interval.

The API client uses the backend's `{code, message, data}` envelope, refreshes
Bearer tokens, downloads attachments from the fixed same-site file endpoint,
and respects `TOO_MANY_ATTEMPTS` responses. Remote executable submission
(`remote_run`) is not wired into the solver; those problems can still be read and
their supplied files downloaded, but solving them may need a separate tool.

For local Python execution instead of Compose:

```sh
uv sync
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
uv run ctf-solve --platform msgctf --list
uv run ctf-solve --platform msgctf --challenges-dir challenges --max-challenges 1 -v
```

uv run ctf-solve --challenges-dir challenges -v
uv run ctf-solve --challenges-dir challenges --max-challenges 20 -v

## How It Works

A **coordinator** LLM manages the competition while **solver swarms** attack individual challenges. Each swarm runs multiple models simultaneously — the first to find the flag wins.

```
                        +-----------------+
                        |  CTFd Platform  |
                        +--------+--------+
                                 |
                        +--------v--------+
                        |  Poller (5s)    |
                        +--------+--------+
                                 |
                        +--------v--------+
                        | Coordinator LLM |
                        | (Claude/Codex)  |
                        +--------+--------+
                                 |
              +------------------+------------------+
              |                  |                  |
     +--------v--------+ +------v---------+ +------v---------+
     | Swarm:          | | Swarm:         | | Swarm:         |
     | challenge-1     | | challenge-2    | | challenge-N    |
     |                 | |                | |                |
     |  GPT-5.6-sol max  | |  GPT-5.6-sol max  | |     ...        |
     +--------+--------+ +--------+-------+ +----------------+
              |                    |
     +--------v--------+  +-------v--------+
     | Docker Sandbox  |  | Docker Sandbox |
     | (isolated)      |  | (isolated)     |
     |                 |  |                |
     | pwntools, r2,   |  | pwntools, r2,  |
     | gdb, python...  |  | gdb, python... |
     +-----------------+  +----------------+
```

Each solver runs in an isolated Docker container with CTF tools pre-installed. Solvers never give up — they keep trying different approaches until the flag is found.

## Quick Start

```bash
# Install
uv sync

# Build sandbox image
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .

# Log in to Codex CLI (used by the default solver/coordinator)
codex login

# Configure CTFd
cp .env.example .env
# Edit .env with your CTFd URL/token

# Run against a CTFd instance
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --challenges-dir challenges \
  --max-challenges 10 \
  -v
```

When a solver confirms a flag, the agent immediately writes `writeup.md` into that
challenge directory using the solver trace and final findings.

## Coordinator Backends

```bash
# Codex coordinator (default, uses your local Codex CLI login)
uv run ctf-solve --coordinator codex ...

# Claude SDK coordinator
uv run ctf-solve --coordinator claude ...
```

## Solver Models

Default model lineup (configurable in `backend/models.py`):

| Model | Provider | Notes |
|-------|----------|-------|
| GPT-5.6-sol | Codex CLI | max reasoning |

## Sandbox Tooling

Each solver gets an isolated Docker container pre-loaded with CTF tools:

| Category | Tools |
|----------|-------|
| **Binary** | radare2, GDB, objdump, binwalk, strings, readelf |
| **Pwn** | pwntools, ROPgadget, angr, unicorn, capstone |
| **Crypto** | SageMath, RsaCtfTool, z3, gmpy2, pycryptodome, cado-nfs |
| **Forensics** | volatility3, Sleuthkit (mmls/fls/icat), foremost, exiftool |
| **Stego** | steghide, stegseek, zsteg, ImageMagick, tesseract OCR |
| **Web** | curl, nmap, Python requests, flask |
| **Misc** | ffmpeg, sox, Pillow, numpy, scipy, PyTorch, podman |

## Features

- **Multi-model racing** — multiple AI models attack each challenge simultaneously
- **Auto-spawn** — new challenges detected and attacked automatically
- **Coordinator LLM** — reads solver traces, crafts targeted technical guidance
- **Cross-solver insights** — findings shared between models via message bus
- **Docker sandboxes** — isolated containers with full CTF tooling
- **Operator messaging** — send hints to running solvers mid-competition

## Configuration

Run `codex login`, then copy `.env.example` to `.env` and fill in your CTFd
settings:

```bash
cp .env.example .env
```

```env
CTFD_URL=https://ctf.example.com
CTFD_TOKEN=ctfd_your_token
```

All CTFd settings can also be passed as environment variables or CLI flags. API
provider settings are only needed if you explicitly select an API-backed model
spec such as `azure/...`, `bedrock/...`, `zen/...`, or `google/...`.

## Requirements

- Python 3.14+
- Docker
- `codex` CLI logged in with `codex login`
- `claude` CLI (optional, only for the Claude coordinator/solver)


