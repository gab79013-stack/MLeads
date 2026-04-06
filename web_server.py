#!/usr/bin/env python3
"""
web_server.py — Production web server entry point for MLeads dashboard

Initializes Flask app from web.app module.
Routes are defined in web/app.py.
Runs on port 5001 by default (use PORT env var to change).

Usage:
  python web_server.py                              # Development server
  gunicorn -w 2 -b 0.0.0.0:5001 web_server:app     # Production
"""

import os
import sys
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("web_server")

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import and initialize Flask app
from web.app import create_app

app = create_app()


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    logger.info(f"🚀 Starting MLeads Web Server on port {port}")
    logger.info(f"   Dashboard: http://localhost:{port}/")
    logger.info(f"   Login: http://localhost:{port}/login.html")
    logger.info(f"   API: http://localhost:{port}/api/")

    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug,
        use_reloader=debug
    )
