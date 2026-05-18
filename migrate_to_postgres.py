#!/usr/bin/env python3
"""
migrate_to_postgres.py — Migrate MLeads from SQLite to PostgreSQL

Usage:
    # Set the PostgreSQL connection URL
    export DATABASE_URL="postgresql://mleads:mleads@localhost:5432/mleads"
    
    # Run migration
    python migrate_to_postgres.py
    
    # Or with custom SQLite path
    python migrate_to_postgres.py --sqlite-path data/leads.db
    
    # Dry run (no writes)
    python migrate_to_postgres.py --dry-run
"""

import argparse
import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("migrate")


def main():
    parser = argparse.ArgumentParser(description="Migrate MLeads SQLite → PostgreSQL")
    parser.add_argument("--sqlite-path", default="data/leads.db", help="Path to SQLite database")
    parser.add_argument("--database-url", default=None, help="PostgreSQL URL (overrides DATABASE_URL env)")
    parser.add_argument("--dry-run", action="store_true", help="Validate schema only, no data migration")
    parser.add_argument("--init-only", action="store_true", help="Only create schema, skip data migration")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    # Check that psycopg2 is available
    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    from db_postgres import init_postgres_db, migrate_from_sqlite

    logger.info("=" * 60)
    logger.info("MLeads SQLite → PostgreSQL Migration")
    logger.info("=" * 60)

    # Step 1: Initialize PostgreSQL schema
    logger.info("\n📋 Step 1: Creating PostgreSQL schema...")
    try:
        init_postgres_db()
        logger.info("✅ Schema created successfully")
    except Exception as e:
        logger.error(f"❌ Schema creation failed: {e}")
        sys.exit(1)

    if args.init_only:
        logger.info("Schema initialized. Skipping data migration (--init-only).")
        sys.exit(0)

    if args.dry_run:
        logger.info("Dry run complete. No data migrated.")
        sys.exit(0)

    # Step 2: Migrate data
    logger.info(f"\n📦 Step 2: Migrating data from {args.sqlite_path}...")
    try:
        success = migrate_from_sqlite(args.sqlite_path)
        if success:
            logger.info("\n✅ Migration completed successfully!")
            logger.info("\nNext steps:")
            logger.info("  1. Set USE_POSTGRES=1 in your .env")
            logger.info("  2. Set DATABASE_URL=postgresql://mleads:mleads@localhost:5432/mleads")
            logger.info("  3. Restart your web server: python web_server.py")
            logger.info("  4. Keep data/leads.db as backup — don't delete it yet")
        else:
            logger.error("❌ Migration returned False — check logs above")
            sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
