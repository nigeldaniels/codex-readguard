# Read before edit

This project uses Codex command hooks to reject edits without evidence of a
complete read of the current target and a review of declared related files.
The implementation uses Python 3.10+ with its standard library on Linux.
No application dependencies, network access, or service restarts are needed.

## Activation

Open this repository in Codex, start a new session, and use `/hooks` to review
and trust the hooks in `.codex/hooks.json`. In the desktop app, use **Settings >
Hooks** or **Review hooks**. Project configuration must also be trusted.
New or changed hook definitions are skipped until trusted; adding these files
does not by itself activate enforcement in an already-running conversation.
Do not use a hook-trust bypass flag to enable the project policy.

See the [official hook documentation](https://learn.chatgpt.com/docs/hooks) for
trust review, tool coverage, and runtime limitations.

## Working on a change

1. Discover the affected code with `rg`, `git grep`, or directory inspection.
   Identify local dependencies/imports, callers, applicable tests, configuration,
   and external message/interface definitions. Follow those relationships until
   the change's behavior and impact are understood. Include all relevant files
   in the review, even when only one will be edited.
2. Check every existing target and related file with `check PATH` before repeating
   a read. A hook status of `complete` means this exact version was already fully
   read in the current context. Otherwise use `read PATH --offset NEXT_OFFSET`
   and follow `next_offset` until it is `null`. Offsets are Unicode character
   positions, not line numbers or byte positions.
3. Declare each target's related-file set and explain why it covers the change.
4. Edit with `apply_patch` or a CLI command. After any approved edit attempt,
   check each target and declare its context again before another patch. Only
   changed or incompletely read files need reading; failed/no-op edits can reuse
   unchanged reads. New user prompts require renewed declarations too.

Example, with each command in a separate tool call from the repository root:

```sh
python3 .codex/guard.py check path/to/target.py
# For unread/changed files start at zero; for partial files use the reported offset.
# Skip these reads entirely when the hook reports complete.
python3 .codex/guard.py read path/to/target.py
python3 .codex/guard.py read path/to/target.py --offset 3000
python3 .codex/guard.py read path/to/dependency.py
python3 .codex/guard.py read path/to/test_target.py
python3 .codex/guard.py context path/to/target.py --related path/to/dependency.py path/to/test_target.py --reason 'Reviewed the local dependency, its caller contract, and the regression tests affected by this change.'
```

Use the returned offset, which can differ from 3000 for Unicode text. Supply
`max_output_tokens: 10000` or more to each `exec_command`, with `tty: false`.
When using code mode, forward the entire result and avoid aggregating many
reads into one truncated outer result. The hook also delivers each verified
chunk as additional model context, independently of the caller's output.

### Reusing a previous read

`check PATH` emits a small version receipt, without source text. The native hook
uses its own session identity to return `READ_GUARD_STATUS` in model context:

| Status | Next action |
| --- | --- |
| `complete` | Reuse the prior read; `next_offset` is `null`. |
| `partial` | Read from the first missing `next_offset`. |
| `changed` | Read the changed file completely, starting at zero. |
| `unread` | Read the file completely, starting at zero. |

The CLI itself does not select a session or claim a cache hit. If no hook status
arrives, use the normal reader and disclose inactive/unavailable enforcement.
A check does not earn new read credit or declare related-file review. It hashes
the current contents rather than trusting modification times; edit checks still
rehash targets and dependencies to detect changes after a successful check.
Ordinary `read PATH` remains available whenever a deliberate reread is useful.

Reader/check/context arguments are always relative to the repository root. From a
subdirectory, invoke the reader with its absolute script path. Patch paths
follow the patch tool's working directory.

Every target needs at least one distinct existing related file, including new
files, deletions, and both paths of a rename. There is no `--no-related` escape.
For documentation or standalone scripts, review the relevant specification,
usage documentation, or tests. Do not supply an unrelated file to satisfy the
count. The reason is a human-reviewable explanation, not proof of relevance.

Ordinary `cat`, `sed`, search snippets, filenames, hashes, and summaries do not
earn read credit. Failed, truncated, or unrecognized tool output does not earn
credit either. A current empty text file still requires its reader receipt.

## Enforcement and lifecycle

- `PreToolUse` checks patch paths and recognized/declared CLI edit targets.
  Existing files
  require full coverage of their current SHA-256 version. Declared related files
  must also be fully read and match their declaration's versions.
- `PostToolUse` verifies complete reader receipts against current file contents.
  Its model context is bounded to 8192 UTF-8 bytes per receipt, below the configured
  10000-token hook context limit. It never trusts a command merely because it ran.
- Related-file declarations are consumed transactionally before allowing an
  edit. Concurrent calls cannot spend the same declaration. Read versions are
  retained, so a failed/cancelled/no-op edit only needs a renewed declaration
  when the file is unchanged.
- New prompts retain read coverage but clear declarations: relevance must be
  assessed for the new request. Session starts/resumes, clears, forks, and
  compaction clear all corresponding evidence. Session end removes it. Separate
  session/transcript/agent identifiers do not share evidence when supplied by
  the runtime. Reuse does not assume old transcript text survived compaction.
- SQLite state lives under ignored `.codex/.read-guard/`. It stores paths,
  hashes, covered ranges, and review explanations; it does not store source text.
- Expected failures and unexpected Python exceptions exit with code 2 to deny
  execution. The hook command also converts interpreter/import failures to 2.

## Shell policy

The purpose is to prevent speculative code edits. There is no command allowlist:
Git (including `-C`, `-c`, and `--no-pager`), `gh`, Docker, package managers,
inspection, tests, ordinary pipelines, substitutions, and TTY commands can run.
This does not grant deployment approval or replace other permission policies.

Common CLI edits receive the same target/context checks as patches: `sed -i`,
`perl -pi`, output redirection, `tee`, file copy/move/delete, explicit Git file
restore/checkout, and literal Python `open(..., 'w')` / `Path(...).write_text(...)`
calls in `-c` scripts and simple heredocs. A command is allowed once its existing
targets and related files have been fully read and their contexts declared.
Quoted search text such as `rg '>'` is not treated as a write.

For opaque scripts, formatters, patch imports, or dynamic write paths, explicitly
declare every file the command can change:

```sh
python3 .codex/guard.py run --writes path/to/target.py -- python3 scripts/update_target.py
python3 .codex/guard.py run --writes path/to/target.py -- black path/to/target.py
python3 .codex/guard.py run --writes path/to/target.py -- git apply change.patch
```

Read and declare context for those targets first. The pre-tool hook checks and
consumes their declarations, then the wrapper runs the command with its original
arguments, working directory, streaming output, and exit status. `--writes`
paths are repository-relative; command arguments follow the working directory.
The wrapper does not sandbox the child or prove the target list is exhaustive.
It is not a standalone permission mechanism when invoked outside Codex.

Ordinary Git history/index/branch operations remain normal Git operations;
explicit file replacement and patch import are treated as edits. Writes outside
this repository and generated directories such as `.git`, `.venv`, `target`,
and `.test-artifacts` are outside this code-review workflow. Maintained files
under `build/` are still protected. See `shell_edits.py` for these boundaries.

Run reader/check/context commands individually. Inspection
commands and a piped/truncated reader do not earn credit. Unknown CLIs are not
automatically prohibited, but agents must still declare their targets when
using them to edit project files.

## Limits and performance

This guard establishes evidence that text was delivered, not that the model
understood it. Related-file discovery is semantic work performed by the agent;
the hook verifies every declared file but cannot prove the declaration includes
every relevant file. The repository instructions require that broader review.

Codex may skip untrusted/disabled hooks, continue after a runtime timeout, or
use tools outside this hook's coverage. MCP writes and specialized tools are not
intercepted by this configuration. Shell recognition is deliberately best effort,
not a full Bash parser or program analysis engine. Aliases, custom CLIs, imported
code, dynamic scripts, and deeply nested shells may hide writes; use declared
targets for such edits. `write_stdin` can send more input without a new pre-tool
check. Test/build runners and other programs can have side effects. Hooks do not
replace an OS sandbox, and a file
can change in the brief interval between checking and applying a patch.

Files must be regular UTF-8 text, at most 2 MiB, inside this repository, without
symlink or parent-traversal paths. Binary files and unsupported files fail with
an explicit explanation instead of silently bypassing the rule. Changes to
such files require a separately designed review workflow.

Each read emits at most 3000 content bytes; cache checks emit only metadata.
Reuse saves model context and avoids repeating source output, not filesystem
hashing. Hashing costs O(file size) per read, cache check, or edit check; shared
dependencies are hashed once per patch. Limits are 64
patch paths, 32 related files per target, and 4096 read/context entries per
session. No whole-repository scan occurs on tool calls. SQLite transactions
serialize evidence updates with a two-second busy timeout; hooks have a ten-second
deadline. Entries older than seven days are pruned during new prompts and
lifecycle resets; expired reads must be repeated even for unchanged files.
SQLite reuses freed pages; the ignored state directory can be removed while
Codex sessions are stopped if disk compaction is ever needed.

## Tests and ownership

```sh
python3 -m unittest discover -s .codex/tests -t .codex -v
```

`guard.py` is the entrypoint. `service.py` orchestrates events; `protocol.py`
defines the CLI/receipt boundary; `files.py` validates and snapshots files;
`patches.py` extracts patch paths; `shell.py`, `shell_edits.py`, and
`python_edits.py` recognize CLI edit targets; `state.py` owns transactional
evidence. Tests run in temporary directories and do not
touch application files or services.
