# Repository Instructions

- Never revert user edits. During coding tasks, if the worktree changes while you are working, assume those changes belong to the user. Do not restore or modify them unless the user explicitly asks.
- Scope edits only to the requested task. If unrelated diffs are present, mention them in the final summary but do not touch them.
- Prefer plain English over unnecessary jargon and technical terminology.
- Follow project-specific rules in `PRODUCT_RULES.md` when that file exists.

## Project Semantic Versioning

The root `VERSION` file is the single authoritative source for the project's product version. Package manifests and lockfiles are tooling metadata and must not be treated as product-version authorities.

The project uses Semantic Versioning in `MAJOR.MINOR.PATCH` form:

- Use `0.x` while the project is experimental or has not reached a coherent MVP.
- Use `1.0.0` for the first coherent, usable MVP or stable public release.
- Increment `MINOR` for meaningful backward-compatible features.
- Increment `PATCH` for fixes, styling refinements, content corrections, and small behavioural improvements.
- Increment `MAJOR` only for a fundamental redesign or an incompatible change.
- Use prerelease identifiers such as `2.0.0-beta.1` when a major or otherwise unstable release needs testing before it becomes final.

After every meaningful batch of project changes, Codex must:

1. Review the actual completed diff.
2. Determine whether the completed work requires no release, a `PATCH`, a `MINOR`, or a `MAJOR` increment.
3. Propose the next version and briefly explain why.
4. Wait for the user's explicit approval before changing `VERSION`, `CHANGELOG.md`, package metadata, Git tags, commits, branches, remotes, deployment services, or any remote repository state.
5. Never choose an increment from lines changed, files changed, or commit count alone.
6. Treat unfinished work as unreleased.
7. Use a prerelease version when the work is not ready for a final release.
8. After release approval, suggest an annotated `vMAJOR.MINOR.PATCH` tag and a concise release commit message.
9. Keep user-facing version displays derived from `VERSION` wherever technically practical.
10. Check whether documentation, deployment metadata, and external integrations require coordinated updates.
11. Distinguish code verification, production-build verification, and live-deployment verification.
12. Never break persisted data or an external connection merely to remove an old version label.

Do not commit, tag, push, amend, create releases, rename or delete branches, change Git remotes, deploy, or otherwise perform Git or external-service write operations without the user's explicit approval.
