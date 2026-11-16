# Readguard project instructions

## Read before edit and review related files

- Before editing, deleting, or renaming any existing file, read its complete
  current contents in the current unreset session context. A verified complete
  read can be reused across ordinary prompts while its content hash matches.
  This applies to source, tests, configuration,
  documentation, and the guard itself. A filename, search excerpt, diff, cached
  summary, or partially truncated output is not a complete read.
- Before every change, discover and completely read all relevant local
  dependencies, callers/consumers, tests, configuration, and interface/message
  definitions. Follow references until the behavior and impact of the change
  are understood. New files require this related-file review too.
- Before repeating a read, use `python3 .codex/guard.py check PATH`. Only a
  hook-provided `READ_GUARD_STATUS` with `status: complete` permits reuse.
  For partial reads, start at its first missing `next_offset`; for changed or
  unread files, start at zero. A check receipt alone does not grant read credit.
- Use the bounded reader and per-target context declaration described in
  `.codex/README.md`: `python3 .codex/guard.py read PATH --offset OFFSET`, follow every
  `next_offset`, then `python3 .codex/guard.py context PATH --related FILE ...
  --reason 'Explain why these files cover the change'`. Include all relevant
  files, not just the minimum one accepted by the hook.
- The purpose is "look before you leap": understand the existing code before
  changing it. Normal Git, CLI, inspection, and test commands are permitted;
  do not impose a general command allowlist in the name of this rule.
- The same complete-read and related-file requirements apply to `apply_patch`
  and CLI edits. Common CLI write targets are checked automatically. For scripts,
  formatters, or other edits whose targets are not apparent, use
  `python3 .codex/guard.py run --writes FILE ... -- COMMAND ARG ...` after reading
  and declaring context for every affected file. List all files the command can
  change; permission to run an unfamiliar command is not evidence of a review.
- Renew each target's related-file declaration after an approved edit attempt
  or a new prompt; reassess which files are relevant to the requested change.
  Reread only files whose content changed or whose reads are incomplete. A
  failed/no-op edit does not invalidate unchanged reads. Session starts, resumes,
  clears, forks, and compaction reset read evidence. Batch coherent edits after reading.
- Do not bypass missing read evidence using alternate tools, hidden writes in
  tests/builds, incomplete target declarations, or forged evidence. Use the
  declared-target workflow for legitimate CLI edits instead of disabling checks.
- Hooks require native Codex trust review. If they are inactive or unavailable,
  disclose that enforcement is inactive and still perform the complete-file and
  related-file review manually. Never claim the guard proves understanding or
  automatically discovers every semantically related file.
