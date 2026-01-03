import sys
import os
from pathlib import Path
import asyncio

# Add project root to sys.path so we can import duck_punch_mcp
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from src.duck_punch_mcp import maps_server

async def verify():
    print("Verifying Google Maps MCP Server...")
    tools = await maps_server.mcp.list_tools()

    if not tools:
        print("FAIL: No tools registered.")
        sys.exit(1)

    print(f"Successfully registered {len(tools)} tools:")
    for tool in tools:
        print(f"- {tool.name}: {tool.description}")

    # Check for expected tools
    expected_tools = ["directions", "geocode", "reverse_geocode", "places"]
    missing = [t for t in expected_tools if not any(registered.name == t for registered in tools)]

    if missing:
        print(f"FAIL: Missing expected tools: {missing}")
        sys.exit(1)

    print("SUCCESS: All expected tools found.")

if __name__ == "__main__":
    asyncio.run(verify())
