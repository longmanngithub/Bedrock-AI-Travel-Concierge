# Project skills for Codex

Open the AI Travel Concierge checkout in Codex. The Skills CLI requires Node.js 22.20 or later. Restore the pinned Matt Pocock and Apple Design copies from the repository root.

```sh
DISABLE_TELEMETRY=1 npx --yes skills@1.9.0 install --agent codex --frozen-lockfile
```

`skills-lock.json` records source commits, file hashes, and copy mode. The downloaded directories under `.agents/skills/` are local to the checkout. The repository includes `.agents/skills/security-review/` directly.

## Choose a skill

Use `/skills` or type `$` to select a skill in Codex. Codex can also select a matching skill from the task description. Open a new session or restart Codex if the new entries are missing.

| Task | Example prompt |
| --- | --- |
| Requirements | `$grill-with-docs` Help me settle the rules for continuing a trip after a replan. |
| Bug fix or TDD | `$diagnosing-bugs` Trace this confirmation loop. Use `$tdd` for a test-first fix. |
| Architecture | `$improve-codebase-architecture` Review the queue and worker interfaces. |
| UI review | `$apple-design` Review sidebar keyboard navigation and narrow-screen layout. |
| Security review | `$security-review` Review the account and trip data ownership checks from source. |

Matt Pocock's setup skill is available as `$setup-matt-pocock-skills`. This checkout already has its local issue tracker, triage, and domain configuration in this directory. See [triage labels](triage-labels.md) for the GitHub label provisioning status.

## Matt Pocock skill inventory

| When to use | Skills |
| --- | --- |
| Clarify a plan, decision, or requirement | `ask-matt`, `grill-me`, `grilling`, `grill-with-docs`, `to-questionnaire` |
| Model a domain, design modules, or explore options | `domain-modeling`, `codebase-design`, `improve-codebase-architecture`, `prototype`, `research` |
| Write specs, create tickets, or coordinate work | `setup-matt-pocock-skills`, `to-spec`, `to-tickets`, `triage`, `wayfinder` |
| Implement, diagnose, test, or review | `implement`, `implement-spec`, `diagnosing-bugs`, `tdd`, `code-review`, `pr` |
| Hand off, teach, or improve a workflow | `handoff`, `retro`, `teach`, `wait-what`, `wizard`, `writing-for-agents` |

## Maintenance

- **Matt Pocock and Apple Design.** For pinned installs, review upstream changes, then rerun `npx skills@1.9.0 add 'OWNER/REPO#REVIEWED_COMMIT' --agent codex --copy --yes --skill NAME [NAME...]` in a clean staging checkout. Use the applicable names in `skills-lock.json` and review any additions to Matt's published plugin manifest. Diff the staged copies against the current installation and preserve local edits before applying the update and new lockfile. Use the frozen restore command above to verify the new pins.
- **Security review.** Maintain the local instructions in its `SKILL.md`. To update Cloudflare's MIT-licensed schema and validators, review the relevant upstream diffs and copy the schema, validators, and matching synthetic tests together. Record the new source commit in the skill and rerun its Node tests.

Use `npx skills@1.9.0 list --agent codex` from the checkout root to inspect the installed files and use Codex's skill picker to verify their discovery.
