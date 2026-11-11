
# DocSense - Multi-Agent Coordination Protocol

## 1. General Principle

GitHub Issues are the source of truth for tasks.
Git branches isolate implementation.
Pull requests control integration.

Never assume another agent's work is complete
unless its status or handoff confirms it.

## 2. Task Ownership

Every implementation task must have:

- A unique GitHub Issue.
- One responsible agent role.
- Clearly defined acceptance criteria.
- Explicit file ownership.
- Dependencies, if any.

An agent must not claim an issue already
owned by another active agent.

## 3. Before Starting Work

The agent must:

1. Read the assigned GitHub Issue.
2. Check its dependencies.
3. Read relevant project documentation.
4. Check whether another agent owns overlapping files.
5. Confirm the task is marked Ready.
6. Report its claim in the GitHub Issue.

## 4. Workspace Isolation

Every implementation agent must work
in its own Git worktree.

Branch naming:

- agent/architect/issue-<number>
- agent/backend/issue-<number>
- agent/frontend/issue-<number>
- agent/qa/issue-<number>

Never edit another agent's worktree.

Never work directly on master.

Never force push.

## 5. File Ownership

Before implementation, each agent must
identify the files it intends to modify.

Agents must not modify files owned by
other active agents.

Shared files require explicit coordination
and approval from the orchestrator.

## 6. Communication

GitHub Issues and Pull Requests are
the shared communication channels.

Agents must communicate:

- When claiming a task.
- When blocked.
- When changing an agreed interface.
- When implementation is complete.
- When tests fail.
- When handing work to another agent.

Every handoff must include:
- Issue number.
- Branch name.
- Files changed.
- Implementation summary.
- Tests executed.
- Known problems.
- Pull request link.

## 7. Dependencies

Never begin a task whose required
dependencies are incomplete.

If blocked:
- Stop implementation.
- Explain the blocker.
- Comment on the GitHub Issue.
- Notify the orchestrator.

## 8. Pull Request Rules

Every implementation task must produce
a Pull Request.

PRs must include:
- Related issue number.
- Summary of changes.
- Testing performed.
- Known limitations.

Agents must not merge their own PRs.

## 9. Orchestrator Responsibilities

The orchestrator must:

1. Break features into independent tasks.
2. Create and organize GitHub Issues.
3. Assign agent roles.
4. Define file ownership.
5. Identify task dependencies.
6. Coordinate agent handoffs.
7. Detect conflicting changes.
8. Track progress.
9. Coordinate reviews and testing.
10. Request human approval before merging.

## 10. Human Approval

The project owner has final authority.

Agents must not independently:
- Change project architecture.
- Modify production secrets.
- Delete important files.
- Merge pull requests.
- Force push.
- Deploy without approval.
