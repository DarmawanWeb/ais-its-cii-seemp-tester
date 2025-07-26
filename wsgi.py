#!/usr/bin/env python3
"""
WSGI entry point for AIS Simulator Flask application
"""

import sys
import os
from app import app

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=False)

# This is what WSGI server will use
application = app