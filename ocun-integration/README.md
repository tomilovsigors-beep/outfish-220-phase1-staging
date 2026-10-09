# Ocún → Outfish: isolated setup (not deployed)

This directory is a preparation scaffold only. No Ocún login automation, inventory sync, or shop import exists yet.

## Credentials

Once a dedicated Render service exists for this integration, open that service's **Environment** section and securely enter:

- `OCUN_BASE_URL` = `https://sales.ocun.com`
- `OCUN_USERNAME` = your Ocún B2B account login
- `OCUN_PASSWORD` = your Ocún B2B password
- `OCUN_READ_ONLY` = `true`
- `OCUN_IMPORT_ENABLED` = `false`

Do not place credentials in GitHub, Google Sheets, chat messages, commit history, or screenshots. Do not enter them in any unrelated existing Outfish service.

## Rollout gates

1. Determine a permitted way to retrieve the Ocún catalog (official API/export, or explicitly authorized login integration).
2. Create a dedicated Render service with minimum needed permissions and protected secrets.
3. Perform read-only login and verify catalog/SKU/variant/availability mappings.
4. Review product data and SEO structure against Outfish's master sheet.
5. Test a limited import on staging; do not change live catalog without explicit approval.

Creating or deploying a service requires selecting appropriate source repository, start/build commands, and trigger settings. This scaffold is deliberately not a runnable integration yet.
