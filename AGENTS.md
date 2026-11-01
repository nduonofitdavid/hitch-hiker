# Repository Instructions for Agents

## Understand the repository first

- Read the README, relevant configuration, and any directory-specific instructions before editing.
- Inspect the affected code and nearby examples to understand structure, naming, spacing, and existing patterns. Read relevant files; do not scan every file unnecessarily.
- Check the working tree for existing changes. Preserve work made by the user or other contributors.

## Indentation and spacing

- Use two spaces per indentation level, not literal tab characters.
- Preserve syntax-required exceptions, such as tabs in Makefile recipes. In indentation-sensitive code, keep the affected block valid without reformatting unrelated code.
- Follow the repository's existing conventions for blank lines, operator spacing, wrapping, and alignment. Keep code readable without excessive whitespace.
- Do not reformat entire files or run repository-wide formatting for a localized change.
- If formatter configuration conflicts with the two-space rule, flag the conflict rather than silently changing repository-wide configuration.

## Make focused edits

- Use targeted patches. Change only the lines or blocks necessary to complete the task, adjusting surrounding indentation when required.
- Do not overwrite or regenerate an existing file from scratch to make a small edit.
- Preserve unrelated code, comments, imports, formatting, and line endings.
- Avoid unrelated renames, file moves, cleanup, and refactors.
- If a substantial rewrite is necessary, explain why and confirm the scope unless the task already authorizes it.

## Keep implementation simple

- Follow the language's coding conventions and the repository's established patterns, subject to the indentation rules above.
- Choose the simplest correct solution that meets the current requirements.
- Reuse existing functions and utilities before introducing new ones.
- Avoid speculative features, unnecessary abstractions, excessive configuration, and premature optimization.
- Add dependencies only when existing code or the standard library cannot reasonably solve the problem.
- Use clear names and straightforward control flow. Add comments to explain non-obvious reasoning, not to narrate obvious code.
- Preserve public interfaces and existing behavior unless the requested change requires otherwise.

## Validate the changes

- Run relevant existing tests, lint checks, or type checks where available. Keep validation proportional to the change.
- Add or update focused tests for changed behavior and bug fixes when useful; avoid tests that merely duplicate implementation details.
- Review the final diff for accidental deletions, unrelated changes, whitespace churn, and debugging leftovers.
- Never claim a check passed unless it was run successfully. Report checks that could not run and explain why.

## Document every change

- Always finish with a concise summary of what changed, why it changed, and how it was validated.
- Update relevant documentation when behavior, public interfaces, configuration, setup, or usage changes.
- Follow the repository's existing changelog convention when applicable. Do not create a separate report or changelog for every trivial edit.
- Keep comments and docstrings consistent with the code. Document important assumptions and known limitations.

## Protect user work and sensitive information

- Do not revert, overwrite, or delete unrelated user changes.
- Do not commit, push, deploy, or perform destructive operations unless authorized by the task.
- Do not open secret-bearing files such as .env files, private keys, or credential files unless explicitly authorized. Use sanitized example configuration when available.
- Never expose credentials in code, logs, documentation, or responses.
- Resolve routine implementation details using repository conventions. Ask for clarification when ambiguity materially affects correctness, scope, or compatibility.
