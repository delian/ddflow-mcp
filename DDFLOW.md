# Project Rules

This file is auto-generated from `.ddflow/rules/` — do not hand-edit it.

## Project Scope

### Config

- **r-company-workflows**: Users and companies can change anything: config, CLI, MCP command or prompt, per company and per project
  - Globs: ddflow/**
  - Priority: 90
- **r-everything-configurable**: No hard-coded tunables: every tunable value is a knob, settable four ways, logged
  - Globs: ddflow/**
  - Priority: 90

### Principle

- **r-everything-managed**: Every definition is a managed record: edit, delete, review, search, RAG, logged, restorable
  - Globs: ddflow/**, integrations/**
  - Priority: 90
- **r-reuse-core**: Reuse the shared core: search before writing, never implement the same behaviour twice
  - Globs: ddflow/**, integrations/**
  - Priority: 90

### Process

- **r-ddflow-self-assessment**: Assess ddflow itself at the end of every agent run, phase and before context compaction; file what is found, without duplicates
  - Globs: ddflow/**, docs/ddflow/**, tests/**
  - Priority: 95

### Size

- **r-small-files**: Keep every file under 20 KB: split into a package of smaller modules
  - Globs: ddflow/**, tests/**
  - Priority: 90
