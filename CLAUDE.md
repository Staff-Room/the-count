# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

"The Count" is a project management and tracking system with Notion integration. This is currently a new repository with minimal setup.

## Project Management Integration

- **Notion**: Used for project tracking and management (MCP connection available)
- **GitHub**: Code repository and version control

## Current State

This is a newly initialized repository with only a README file. The codebase structure and development commands will need to be established as the project develops.

## Notion Integration

The repository is configured to work with Notion through MCP (Model Context Protocol). This allows direct interaction with Notion workspaces for project management tasks.

### Project Management Hierarchy
- **Project**: "The Count" - top-level project management
- **Tasks/Milestones**: Multi-day objectives within the project
- **Activities**: Focused work sessions (what we update during active development)

### Working Session Protocol
When working on this project, Claude Code should:
- Always identify and update the current **Activity** page in Notion
- Reference the related **Task/Milestone** being worked on
- Keep the **Project** context and direction in mind
- Update progress markers, checklists, and steps as work is completed
- Use Notion MCP to maintain synchronized project management throughout development sessions

### Creating Activities from Template
To create new activities: fetch template content (ID: `15f592a30352800998baef9f9bcf83dd`) → create page with `notion-create-pages` → set relation properties using full URLs. Note: use create-pages, not duplicate-page.

The Notion project page URL: https://www.notion.so/26e592a3035280ebbe93cfe1d58af13a

## Agent skills

### Issue tracker

Issues and PRDs are tracked as GitHub issues (`Staff-Room/the-count`) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles, mapped 1:1 to identically-named GitHub labels (only `wontfix` exists today; the other four need creating). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` (created lazily by `/grill-with-docs`) + the existing `docs/adr/` at the repo root. See `docs/agents/domain.md`.