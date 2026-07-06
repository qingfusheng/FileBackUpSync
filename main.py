"""Compatibility entry point for the backup-sync command."""

from backup_sync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
