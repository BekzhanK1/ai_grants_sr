This directory contains JSON reference data exported from the database.

Expected files:
- `menus.json`
- `groups.json`
- `grants.json`

They are loaded into memory on application startup to help the AI map natural
language requests to concrete IDs (menu_id, group_id, grant_id, etc.).

