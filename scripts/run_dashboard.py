#!/usr/bin/env python3
"""
Myrium Dashboard Launcher

Launches the Streamlit dashboard for the Myrium BI system.
"""

import sys
import subprocess
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DASHBOARD_PATH = PROJECT_ROOT / "src" / "dashboard" / "app.py"


def main():
    """Launch the Streamlit dashboard."""
    import argparse

    parser = argparse.ArgumentParser(description="Launch the Myrium dashboard")
    parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="Port to run the dashboard on (default: 8501)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Host to bind to (default: localhost)"
    )

    args = parser.parse_args()

    print(f"Starting Myrium Dashboard...")
    print(f"URL: http://{args.host}:{args.port}")
    print(f"Press Ctrl+C to stop")
    print("-" * 50)

    # Change to project directory for proper imports
    import os
    os.chdir(PROJECT_ROOT)

    # Launch Streamlit
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(DASHBOARD_PATH),
        "--server.port", str(args.port),
        "--server.address", args.host,
        "--browser.gatherUsageStats", "false"
    ]

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    except subprocess.CalledProcessError as e:
        print(f"Error launching dashboard: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
