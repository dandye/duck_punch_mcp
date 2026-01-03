import sys
import os
import inspect
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment variables
load_dotenv()

# Add external submodule to path
current_dir = Path(__file__).parent
project_root = current_dir.parent.parent
external_path = project_root / "external" / "google-maps-services-python"
sys.path.append(str(external_path))

try:
    import googlemaps
except ImportError:
    print(f"Error: Could not import googlemaps from {external_path}", file=sys.stderr)
    sys.exit(1)

# Initialize MCP Server
mcp = FastMCP("Google Maps")

# Initialize Google Maps Client
api_key = os.getenv("GOOGLE_MAPS_API_KEY")
if not api_key:
    print("Warning: GOOGLE_MAPS_API_KEY not set. Client initialization might fail or limits will apply.", file=sys.stderr)
    # Some methods might work without key or we want to allow server to start for inspection
    # But Client __init__ raises ValueError if no key/client_id
    # We'll use a dummy key if missing to allow introspection, but actual calls will fail
    # The key must start with "AIza" to pass validation
    api_key = "AIzaDummyKeyForIntrospection"

try:
    client = googlemaps.Client(key=api_key)
except Exception as e:
    print(f"Error initializing Google Maps Client: {e}", file=sys.stderr)
    sys.exit(1)

# List of methods to ignore (internal or standard object methods)
IGNORED_METHODS = {
    "__init__",
    "clear_experience_id",
    "get_experience_id",
    "set_experience_id"
}

def register_tools():
    """Dynamically register Google Maps Client methods as MCP tools."""

    # Get all members of the client instance
    members = inspect.getmembers(client)

    for name, method in members:
        # We only want callable methods that are not private/protected
        if (not name.startswith("_")
            and name not in IGNORED_METHODS
            and callable(method)):

            # The client.py implementation adds methods like `directions`, `geocode` etc.
            # dynamically using `make_api_method`.
            # We want to expose these.

            try:
                # We wrap the method to provide a proper docstring if needed and ensure it's bound
                # Note: `method` here is already a bound method of `client` instance

                # FastMCP uses the function signature and docstring.
                # The dynamically added methods in googlemaps seem to wrap the original functions
                # using functools.wraps, so they should preserve metadata.

                mcp.tool(name=name)(method)
                print(f"Registered tool: {name}")

            except Exception as e:
                print(f"Failed to register tool {name}: {e}", file=sys.stderr)

# Run registration
register_tools()

if __name__ == "__main__":
    mcp.run()
