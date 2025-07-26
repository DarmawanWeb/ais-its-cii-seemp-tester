#!/usr/bin/env python3
"""
WSGI entry point for AIS Simulator Flask application
"""

import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(__file__))

from app import app

if __name__ == "__main__":
    app.run()

# This is what WSGI server will use
application = app