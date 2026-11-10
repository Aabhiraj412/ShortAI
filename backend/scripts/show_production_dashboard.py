"""
CLI tool to inspect the CrazzyDocs Production Health Dashboard.
"""
import sys
import os

WORKSPACE = r"D:\Agents\CrazzyDocs"
sys.path.insert(0, os.path.join(WORKSPACE, "backend"))

from backend.app.services.production_health_dashboard import production_health_dashboard


def main():
    dashboard = production_health_dashboard.render_console_dashboard()
    print(dashboard)


if __name__ == "__main__":
    main()
