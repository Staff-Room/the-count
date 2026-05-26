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
    from dotenv import load_dotenv

    load_dotenv()
    plaid_env = os.getenv("PLAID_ENVIRONMENT", "sandbox")

    print("🧛 Starting The Count development server...")
    print(f"🏦 Plaid environment: {plaid_env}")
    print("📊 Open the dashboard: http://localhost:5001/dashboard")
    print("📝 Connect accounts via Plaid Link, then sync to SQLite / Notion worker")
    print("-" * 50)
    
    app.run(debug=True, host="0.0.0.0", port=5001)