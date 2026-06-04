# BioIT Web App

This folder contains the first website shell for the BioIT genome pipeline.

## Purpose

The app is designed to sit on top of the deployed AWS pipeline and provide:

- job submission
- pipeline status visibility
- chromosome availability tracking
- analytics entry points
- region visualization hooks

## Current State

This is a frontend starter, not a deployed production app yet.

It currently includes:

- `index.html`
- `styles.css`
- `app.js`

## Planned Backend

The frontend is intended to talk to a future API layer with endpoints like:

- `POST /api/jobs`
- `POST /api/jobs/human-reference`
- `GET /api/status/overview`
- `GET /api/chromosomes`
- `GET /api/results`
- `POST /api/query`

## Open Locally

You can open `index.html` directly in a browser for a visual prototype.

## Suggested Next Step

Add a new Lambda-backed API and wire the forms in `app.js` to real AWS endpoints.
