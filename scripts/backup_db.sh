#!/bin/bash
# Backup MLeads database
DATE=$(date +%Y%m%d)
BACKUP_DIR="/opt/data/MLeads/backups"
DB_PATH="/opt/data/MLeads/data/leads.db"

mkdir -p $BACKUP_DIR
cp $DB_PATH "$BACKUP_DIR/leads_$DATE.db"

echo "Backup completed: $BACKUP_DIR/leads_$DATE.db"
# Optional: upload to cloud storage
# rclone copy $BACKUP_DIR remote:backups/mleads