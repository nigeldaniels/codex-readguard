# Codex Readguard

**Read the fucking code!** A project-local hook for Codex that checks for complete
reads of the current file and its declared related files before allowing edits.
It helps prevent speculative changes based on filenames, snippets, or stale code.

[![Tests](https://github.com/nigeldaniels/codex-readguard/actions/workflows/tests.yml/badge.svg)](https://github.com/nigeldaniels/codex-readguard/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## What it does

- Checks patches and recognized CLI edits against verified full-file reads.
- Requires a related-file review covering dependencies, callers, tests, and config.
- Reuses complete reads when the file's SHA-256 hash has not changed.
- Resumes partial reads at their first missing section.
- Keeps ordinary Git commands, inspection, and test runners available.
- Supports explicit write declarations for scripts and other opaque CLI edits.
- Stores session evidence locally, without storing source contents in its database.

This is a workflow guardrail. It verifies that text was delivered to the model;
it cannot prove understanding or discover every relevant dependency automatically.

## Requirements

- Linux, Git, and Python 3.10 or newer; no pip dependencies.
- A local Codex client that supports project command hooks and native hook trust.
- A trusted Git repository where you want Readguard installed.

The original integration was checked with Codex CLI 0.169.0. Hook APIs and tool
coverage can change; review the [Codex hook documentation](https://learn.chatgpt.com/docs/hooks).
Windows is unsupported. macOS has not been validated.

## Install

Clone this repository, review the code, then point the installer at your project's
Git root. Replace `/path/to/your-project` with its actual path:

```sh
git clone https://github.com/nigeldaniels/codex-readguard.git
cd codex-readguard
python3 install.py /path/to/your-project --dry-run
python3 install.py /path/to/your-project
```

The installer adds `.codex/guard.py`, `.codex/read_guard/`, `.codex/hooks.json`,
`.codex/README.md`, and `.codex/.gitignore`. It refuses to replace existing files
and never copies runtime evidence, credentials, or local Codex configuration.
Keep the destination project idle while installing. Files created by a failed
installation are rolled back; any cleanup failure is reported explicitly.

Then:

1. Merge [examples/AGENTS.md](examples/AGENTS.md) into your project's `AGENTS.md`,
   preserving its existing instructions. The installer does not change it.
2. Open your project in Codex and review/trust the five hooks using `/hooks`.
   The desktop interface exposes **Settings > Hooks** or **Review hooks**.
3. Start a new session if an existing conversation has not loaded the hooks.
4. Keep `.codex/.read-guard/` ignored; it contains local session evidence.

Installing files does not grant native hook trust. The installer does not change
your Codex permissions or trust settings.

### Existing configuration or upgrades

The installer stops before writing if any destination already exists. For an
existing setup, review and merge the changes manually:

- Copy `guard.py` and the `read_guard` package after reviewing any local changes.
- Merge each event group in this repository's `.codex/hooks.json` into your own
  configuration without dropping unrelated hooks or duplicating Readguard handlers.
- If you use inline hooks in `.codex/config.toml`, merge the equivalent handlers
  there instead of registering them twice. Review both project and global hooks.
- Merge the usage documentation and ignore patterns into the corresponding files.
- Include the MIT license with installed code (the installer places a copy at
  `.codex/read_guard/LICENSE`).
- Revisit `/hooks` whenever hook definitions change.

Commit the installed files and merged instructions with your project so teammates
can review them. Each teammate must trust the hooks in their own Codex client.

## How Codex uses it

Run each command individually from the target repository root. Use real paths
from that project. The `check` response from the hook determines whether reading
is needed:

```sh
python3 .codex/guard.py check src/example.py
```

| Hook status | What to do |
| --- | --- |
| `complete` | Reuse the complete read of this unchanged file. |
| `partial` | Read from the returned `next_offset`. |
| `changed` or `unread` | Start reading at offset zero. |

When a read is needed:

```sh
python3 .codex/guard.py read src/example.py --offset 0
```

Follow every returned `next_offset` until it is `null`. Read all relevant files
the same way, then declare the review for each file that will change:

```sh
python3 .codex/guard.py context src/example.py --related src/dependency.py tests/test_example.py --reason 'Reviewed the dependency contract and tests covering the requested behavior.'
```

Use `apply_patch` or a recognized CLI edit. For an opaque command, list every file
it can change before running it:

```sh
python3 .codex/guard.py run --writes src/example.py -- python3 scripts/update_example.py
```

The wrapper depends on the native pre-tool hook; running it in a terminal is not
a standalone enforcement mechanism. Likewise, `check` alone cannot look up a
trusted session. Only the hook's `READ_GUARD_STATUS` can authorize read reuse.
Without that response, use a normal full read and report missing enforcement.

## Cache and lifecycle

Read evidence is scoped to the session, transcript, and agent identifiers supplied
by Codex. Ordinary prompts and failed/no-op edits preserve unchanged reads.
New prompts and approved edit attempts require a renewed related-file declaration.
Changed contents require a fresh full read. Session starts, resumes, clears,
forks, and compaction reset evidence; session end removes it. Evidence older than
seven days is pruned at prompt/lifecycle boundaries.

Content hashes, not timestamps, establish freshness. Checking still reads files
from disk; caching saves repeated source output and model context. A check does
not itself earn read credit or replace semantic review.

## Boundaries

The guard handles regular UTF-8 files up to 2 MiB within the project. Symlinks,
binary files, and parent-traversal paths are rejected. Each target needs at least
one distinct related file. New files and renames also need related-file review.

Shell recognition is best effort, not a complete Bash parser. Custom CLIs,
dynamic scripts, and specialized tool paths can hide writes. Agents must declare
opaque edits. A hook may be skipped, disabled, time out, or miss an unsupported
tool. A file can also change between validation and the edit itself. Readguard
does not replace an OS sandbox, permission policy, tests, or human review.

See [.codex/README.md](.codex/README.md) for the complete protocol, limits, and
operating instructions.

## Development

```sh
python3 -m unittest discover -s .codex/tests -t .codex -v
python3 -m unittest discover -s tests -v
```

Tests use temporary repositories and synthetic hook events. They include the
actual configured hook command, but do not start an LLM or prove that a particular
Codex conversation has hooks active. GitHub Actions runs both suites on Linux.

`guard.py` dispatches CLI and hook requests. `service.py` coordinates command/path
recognition, file snapshots, and transactional SQLite evidence. `protocol.py`
defines bounded receipts; `files.py` validates paths and computes hashes.
`install.py` delegates installation to `installer.py`.

MIT licensed; see [LICENSE](LICENSE).
