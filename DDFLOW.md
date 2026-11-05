# Project Rules

This file is auto-generated from `.ddflow/rules/` — do not hand-edit it.

## Project Scope

### Architecture

- **r-adapters**: Every external dependency goes through a port in ddflow/adapters: one implementation per function, a new port touches only its own files
  - Globs: ddflow/**
  - Priority: 100

### Config

- **r-company-workflows**: Users and companies can change anything: config, CLI, MCP command or prompt, per company and per project
  - Globs: ddflow/**
  - Priority: 90
- **r-everything-configurable**: No hard-coded tunables: every tunable value is a knob, settable four ways, logged
  - Globs: ddflow/**
  - Priority: 90

### Locking

- **r-lock-cycle-selfheal**: ddflow heals an undetected circular wait by adding needs edges; agents never poll-wait or hold while waiting
  - Globs: ddflow/**, docs/ddflow/**, tests/**
  - Priority: 94

### Principle

- **r-everything-managed**: Every definition is a managed record: edit, delete, review, search, RAG, logged, restorable
  - Globs: ddflow/**, integrations/**
  - Priority: 90
- **r-no-shell**: No shell needed: every command and acceptance works over MCP and chat; a shell-less server instructs the agent what to run
  - Globs: ddflow/**, integrations/**
  - Priority: 90
- **r-reuse-core**: Reuse the shared core: search before writing, never implement the same behaviour twice
  - Globs: ddflow/**, integrations/**
  - Priority: 90
- **r-tell-dont-discover**: Tell the agent what to run: every result, refusal and error names the exact next call (MCP and CLI form); help is generated, not typed
  - Globs: ddflow/**, integrations/**
  - Priority: 90

### Process

- **r-ddflow-self-assessment**: Assess ddflow itself at the end of every agent run, phase and before context compaction; file what is found, without duplicates
  - Globs: ddflow/**, docs/ddflow/**, tests/**
  - Priority: 95

### Size

- **r-small-files**: Split any Python file over 30 KB into a module or package: small files lock fewer parallel agents and fit small context windows
  - Globs: ddflow/**, tests/**
  - Priority: 90
