# Backup And Restore

Use managed PostgreSQL point-in-time recovery plus daily encrypted logical backups. Back up object
storage separately once charts are enabled.

Basic drill:

```powershell
pg_dump --format=custom --file=market-monitor.dump $env:POSTGRES_DSN
createdb market_monitor_restore
pg_restore --clean --if-exists --dbname=market_monitor_restore market-monitor.dump
```

Run `alembic current`, application import checks, and a read-only setup/proof query against the
restored database. Record recovery time and data-loss window. Never test restore by overwriting the
production database.
