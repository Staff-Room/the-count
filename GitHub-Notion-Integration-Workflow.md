# GitHub-Notion Integration Workflow

This document outlines the process for setting up a new repository and connecting it to a Notion project management system.

## Project Structure Overview

**Hierarchy:**
- **Project** (top-level): Overall project management and direction
- **Tasks/Milestones** (mid-level): Multi-day objectives within the project  
- **Activities** (bottom-level): Focused work sessions where you sit down and actively work

## Setup Workflow

### 1. Create GitHub Repository
- Initialize repository with README
- Push to GitHub organization (e.g., Staff-Room)
- Note the repository URL for later use

### 2. Create CLAUDE.md File
- Run `/init` in Claude Code to analyze codebase and create CLAUDE.md
- Commit and push the file to repository

### 3. Connect Notion MCP
- Authenticate Notion connection in Claude Code using `/mcp`
- Verify connection is working

### 4. Link Repository to Notion Project
- Find the Notion project page
- Update the "Github Repo" property with the repository URL
- Verify the connection is established

### 5. Set Up GitHub Issues Sync (Optional)
- Configure GitHub issues to sync to Notion project
- Note: Typically one-way sync (GitHub → Notion)
- Cannot create GitHub issues directly from Notion

## Working Session Guidelines

During active development sessions:
- Always work within the context of an **Activity** page in Notion
- Keep the Activity page updated with progress and steps
- Reference the related **Task/Milestone** being worked on
- Maintain awareness of overall **Project** direction
- Update checklists and progress markers as work is completed

## Claude Code Integration Notes

Claude Code should proactively:
- Update Activity pages during work sessions
- Check in on related Tasks and Project status
- Maintain context of current work within the larger project hierarchy
- Use Notion MCP to keep project management synchronized with development work