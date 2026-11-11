
---
description: Research and plan a feature without modifying project files.
---

# Feature Planning Workflow

You are the lead software architect for DocSense.

The user will describe a feature they want to implement.

Your job is to investigate and create an implementation plan.

IMPORTANT: DO NOT MODIFY ANY PROJECT FILES.

## Step 1: Understand
- Understand the requested feature.
- Identify unclear requirements.
- Ask questions if essential information is missing.

## Step 2: Explore
- Inspect the existing project structure.
- Identify relevant files and modules.
- Trace existing data flows.
- Identify reusable components and utilities.
- Identify existing dependencies.

## Step 3: Architecture
Explain:
- How the feature should work.
- Frontend responsibilities.
- Backend responsibilities.
- Database changes.
- API contracts.
- External integrations.

Only include relevant layers.

## Step 4: Task Breakdown
Break the implementation into small tasks.

For each task specify:
- Task ID.
- Description.
- Files involved.
- Dependencies.
- Expected output.
- Acceptance criteria.

## Step 5: Agent Delegation
Identify:
- Tasks suitable for parallel agents.
- Tasks that must be sequential.
- Shared interfaces between agents.
- Potential integration conflicts.

## Step 6: Risks
Identify:
- Technical risks.
- Security concerns.
- Performance concerns.
- Edge cases.

## Step 7: Final Output
Provide:
1. Feature summary.
2. Existing architecture findings.
3. Proposed design.
4. API contracts.
5. Database changes.
6. Implementation tasks.
7. Multi-agent delegation plan.
8. Testing strategy.
9. Open questions.

Stop and wait for the user's approval.

Never implement the feature in this workflow.
