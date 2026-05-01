#!/usr/bin/env python3
"""
The Count - Development Server Runner
Quick script to run the Flask development server
"""

import os
import sys
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent / "src" / "backend"
sys.path.insert(0, str(src_path))

# Import and run the Flask app
from app import app

if __name__ == "__main__":
    print("🧛 Starting The Count development server...")
    print("📊 Open the dashboard: http://localhost:5001/dashboard")
    print("📝 Set up .env with Plaid credentials, then connect accounts and sync")
    print("-" * 50)
    
    app.run(debug=True, host="0.0.0.0", port=5001)