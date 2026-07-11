# O's flow React-admin

This folder contains the manifest-driven React-admin scaffold for O's flow V2.

## What it uses

- `GET /admin/react-admin` for the resource manifest
- The existing API surface for list / show / edit / delete / reprocess / reassign operations

## Local run

```powershell
cd frontend/react-admin-app
npm install
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev
```

## Included surfaces

- Cases
- Documents
- Invoices
- Missing submissions
- Notification deliveries
- Operation logs

## Supported case/document actions

- Case reprocess
- Document reassign
- Document reprocess
- Document delete

This scaffold is intentionally thin: it keeps the manifest as the source of truth and lets the backend determine which resources and fields are visible.
