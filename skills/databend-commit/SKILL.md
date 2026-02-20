---
name: databend-commit
description: >
  Lint and commit staged changes following Conventional Commits.
  Use when the user wants to commit, make a commit, or says "commit".
  Trigger on: "commit", "commit changes", "make a commit", "lint and commit".
---

# Databend Commit

Lint staged changes and create a Conventional Commits message.

## Workflow

### Step 1: Run Lint

```bash
make lint
```

- Timeout: 600000ms (10 minutes).
- If lint fails, fix the issues and re-run until it passes.
- Do NOT proceed to commit until lint is clean.

### Step 2: Review Changes

Run `git diff --cached --stat` and `git diff --cached` to understand what changed.
If nothing is staged, run `git add -p` interactively or ask the user what to stage.

### Step 3: Generate Commit Message

Follow the [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) spec:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

**Types** (pick the most appropriate):
- `feat` — new feature
- `fix` — bug fix
- `refactor` — code restructuring, no behavior change
- `perf` — performance improvement
- `docs` — documentation only
- `test` — adding or updating tests
- `ci` — CI/CD changes
- `build` — build system or dependency changes
- `chore` — other changes that don't modify src or test
- `style` — formatting, whitespace, no code change

**Rules:**
- Type is required, scope is optional (in parentheses).
- Description: imperative mood, lowercase first letter, no period at end.
- Keep the first line under 72 characters.
- Add body (separated by blank line) only if the "what" and "why" aren't obvious from the description.
- Use `BREAKING CHANGE:` footer or `!` after type/scope for breaking changes.
- Scope should reflect the affected module (e.g., `query`, `meta`, `storage`, `logging`).

### Step 4: Commit

```bash
git commit -m "<message>"
```

Use `-m` for simple commits. For commits with body/footer, use `git commit` with a temp file or multi-line `-m`.

### Step 5: Show Result

Run `git log --oneline -1` to confirm the commit was created.
