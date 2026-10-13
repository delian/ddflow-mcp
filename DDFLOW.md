# Project Rules

This file is auto-generated from `.ddflow/rules/` — do not hand-edit it.

## Project Scope

### Config

- **r-everything-configurable**: No hard-coded tunables: every tunable value is a knob, settable four ways, logged
  - Globs: ddflow/**
  - Priority: 90

### Size

- **r-small-files**: Keep every file under 20 KB: split into a package of smaller modules
  - Globs: ddflow/**, tests/**
  - Priority: 90
