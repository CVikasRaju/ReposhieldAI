#!/usr/bin/env python3
"""Isolation verification script (Phase 9).

Prints PASS or FAIL for each of the five isolation checks.
"""

import sys
import os
import subprocess
import requests

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import get_settings


def check_workspace_not_mounted() -> bool:
    """Check that workspace is not mounted inside the agent container."""
    workspace = get_settings().workspace_abs
    # In a real agent container, /app/workspace should not exist
    # Here we just check it doesn't exist at the default path
    if os.path.exists(str(workspace)):
        print(f"FAIL: Workspace exists at {workspace}")
        return False
    print("PASS: Workspace not mounted")
    return True


def check_no_internet() -> bool:
    """Check that internet connectivity is blocked."""
    try:
        response = requests.get("https://example.com", timeout=3)
        print(f"FAIL: Internet is accessible (status {response.status_code})")
        return False
    except (requests.ConnectionError, requests.Timeout):
        print("PASS: No internet connectivity")
        return True


def check_no_collector() -> bool:
    """Check that collector connection is blocked."""
    try:
        response = requests.get("http://collector.local:9999", timeout=3)
        print(f"FAIL: Collector is accessible (status {response.status_code})")
        return False
    except (requests.ConnectionError, requests.Timeout):
        print("PASS: Collector connection blocked")
        return True


def check_api_needs_token() -> bool:
    """Check that API routes require the approver token."""
    try:
        response = requests.get("http://localhost:8000/api/events", timeout=3)
        if response.status_code == 401:
            print("PASS: API requires approver token")
            return True
        else:
            print(f"FAIL: API returned status {response.status_code}, expected 401")
            return False
    except (requests.ConnectionError, requests.Timeout):
        print("FAIL: Gateway not reachable")
        return False


def check_gateway_reachable() -> bool:
    """Check that the gateway MCP endpoint is reachable."""
    try:
        response = requests.get("http://localhost:8001/mcp", timeout=3)
        # The MCP listener should be up; check for reasonable response
        print("PASS: Gateway MCP endpoint reachable")
        return True
    except (requests.ConnectionError, requests.Timeout):
        print("FAIL: Gateway MCP endpoint not reachable")
        return False


def main() -> int:
    """Run all isolation checks and print results."""
    checks = [
        ("Workspace not mounted", check_workspace_not_mounted),
        ("No internet", check_no_internet),
        ("No collector", check_no_collector),
        ("API needs token", check_api_needs_token),
        ("Gateway reachable", check_gateway_reachable),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"ERROR: {name} - {e}")
            results.append((name, False))
    
    # Print summary
    print("\n--- Isolation Check Summary ---")
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"{passed}/{total} checks passed")
    
    if passed == total:
        print("ALL CHECKS PASSED")
        return 0
    else:
        print("SOME CHECKS FAILED")
        for name, result in results:
            if not result:
                print(f"  - {name}: FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
