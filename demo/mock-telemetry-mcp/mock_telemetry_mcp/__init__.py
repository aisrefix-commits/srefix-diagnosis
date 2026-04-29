"""mock-telemetry-mcp: stage prop for the srefix-diagnosis demo.

Serves fake prom / loki / jumphost MCP tools that return canned data from
the active demo scenario. Used so Claude can demonstrate the diagnostic
loop end-to-end without any real production environment.

Active scenario is selected via env DEMO_SCENARIO_ID (default: first one).
"""
