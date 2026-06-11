# BioIT Web App

This folder contains the deployed dashboard frontend for the BioIT genome pipeline.

## What Lives Here

- `index.html` - full dashboard experience
- `compact.html` - reviewer-friendly compact dashboard route
- `styles.css` - shared styling for both dashboard pages
- `app.js` - shared client logic, data loading, and visualization behavior
- `config.js.example` - local template for the API base URL

At deploy time, Terraform publishes this folder to the dashboard S3 bucket and serves it through CloudFront.

## Runtime Model

The frontend talks to the deployed dashboard API for:

- chromosome inventory
- per-chromosome summary data
- pattern and region tables
- batch/full-analysis status
- job submission actions

The live deployment currently uses:

- full dashboard: `index.html`
- compact dashboard: `compact.html`

Both pages share the same `app.js` and `styles.css`.

## Local Config

For local or manual testing, create `config.js` from the example file and point it at the deployed API:

```bash
cp config.js.example config.js
```

Then set:

```js
window.BIOIT_API_BASE_URL = "https://<api-id>.execute-api.<region>.amazonaws.com";
```

Terraform can also generate and upload `config.js` during deployment.

## Open Locally

You can open either page directly in a browser for static inspection:

- `index.html`
- `compact.html`

For full live behavior, the pages need a reachable API base URL in `config.js`.

## Deployment Notes

- Source of truth is the root `webapp/` folder in the repo.
- `build.sh` copies this folder into `dist/terraform/webapp` when packaging deploy artifacts.
- The deployed CloudFront dashboard should stay aligned with the files in this directory.
