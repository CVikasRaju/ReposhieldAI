#!/usr/bin/env bash
# RepoShield AI - 1-Click Launch Script (Linux/macOS)

echo "======================================================================"
echo "                     RepoShield AI - Zero-Trust Shield"
echo "======================================================================"
echo ""

# 1. Resolve Python
if [ -f ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "[ERROR] Python 3.11+ is required."
    exit 1
fi

# 2. Token
export RS_APPROVER_TOKEN="${RS_APPROVER_TOKEN:-reposhield-approver-token}"

# 3. Ensure .aishield exists
if [ ! -f ".aishield" ]; then
    echo "[*] Initializing default .aishield policy file..."
    $PY -m backend.main --init .
fi

# 4. Start Gateway
echo "[*] Starting RepoShield Gateway (MCP :8001 / API :8000)..."
$PY -m backend.main &
GATEWAY_PID=$!

# 5. Start Frontend
echo "[*] Starting RepoShield Web Dashboard (:5173)..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

sleep 3

# 6. Open Browser
DASHBOARD_URL="http://localhost:5173/#token=$RS_APPROVER_TOKEN"
echo "[*] Opening browser: $DASHBOARD_URL"
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$DASHBOARD_URL"
elif command -v open >/dev/null 2>&1; then
    open "$DASHBOARD_URL"
fi

echo ""
echo "======================================================================"
echo " STATUS: RepoShield AI is active and protecting your workspace!"
echo "======================================================================"
echo "  - Web Dashboard:  $DASHBOARD_URL"
echo "  - MCP Endpoint:   http://127.0.0.1:8001/mcp"
echo "  - Approver Token: $RS_APPROVER_TOKEN"
echo ""
echo "Press [Ctrl+C] to stop all RepoShield services..."

trap "echo ''; echo '[*] Stopping services...'; kill $GATEWAY_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
