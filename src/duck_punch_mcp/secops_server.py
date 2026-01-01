
import os
import importlib
import inspect
import functools
import pkgutil
import sys
import json

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Add external submodules to path
# We assume the project root is 3 levels up from this file (src/duck_punch_mcp/secops_server.py)
# Actually, if we run with `python -m duck_punch_mcp.secops_server` from root, we need to add external/secops-wrapper/src
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
secops_path = os.path.join(project_root, "external/secops-wrapper/src")

if secops_path not in sys.path:
    sys.path.append(secops_path)

try:
    from secops.chronicle.client import ChronicleClient
    import secops.chronicle
except ImportError as e:
    sys.stderr.write(f"Error importing secops-wrapper: {e}\n")
    sys.stderr.write(f"Ensure external/secops-wrapper is populated (git submodule update --init --recursive)\n")
    # We might want to exit or continue with limited functionality if possible, but for now lets fail hard on import if critical deps missing
    # But for tool discovery we might need to be softer?
    # Let's re-raise for now.
    raise

# Initialize Chronicle Client
project_id = os.environ.get("PROJECT_ID")
customer_id = os.environ.get("CUSTOMER_ID")
region = os.environ.get("CHRONICLE_REGION", "us")

# Initialize FastMCP
mcp = FastMCP("SecOps")

# Global client instance
_client = None

def get_client():
    global _client
    if _client is None:
        if not project_id or not customer_id:
            # If not set, we can't really function as a tool that requires auth
             pass

        if project_id and customer_id:
            _client = ChronicleClient(project_id=project_id, customer_id=customer_id, region=region)

    if _client is None:
         raise ValueError("PROJECT_ID and CUSTOMER_ID environment variables must be set.")

    return _client

def discover_tools():
    """Dynamically discover tools in secops.chronicle package"""
    tools = []

    # Reload to ensure we have the latest
    import secops.chronicle

    for module_info in pkgutil.iter_modules(secops.chronicle.__path__):
        if module_info.name == "client":
            continue

        module_name = f"secops.chronicle.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            continue

        for name, obj in inspect.getmembers(module):
            if not inspect.isfunction(obj):
                continue

            if name.startswith("_"):
                continue

            if obj.__module__ != module_name:
                continue

            try:
                sig = inspect.signature(obj)
                params = list(sig.parameters.values())

                if not params:
                    continue

                # Check if first argument is likely 'client'
                first_param = params[0]
                if first_param.name == "client":
                    tools.append((module_name, name, obj))
            except ValueError:
                continue
    return tools

def load_overrides():
    """Load documentation overrides from mcp_docs/overrides.json"""
    overrides = {}
    try:
        # Look for mcp_docs relative to this file
        docs_dir = os.path.join(os.path.dirname(__file__), "mcp_docs")
        overrides_file = os.path.join(docs_dir, "overrides.json")

        if os.path.exists(overrides_file):
            with open(overrides_file, 'r') as f:
                mapping = json.load(f)

            for func_path, md_file in mapping.items():
                md_path = os.path.join(docs_dir, md_file)
                if os.path.exists(md_path):
                    with open(md_path, 'r') as f:
                        overrides[func_path] = f.read()
    except Exception as e:
        print(f"Warning: Failed to load overrides: {e}")

    return overrides

def register_tools():
    tools = discover_tools()
    overrides = load_overrides()

    for module_name, func_name, func in tools:
        try:
            # Wrapper to inject client
            # We need to preserve signature but remove 'client'

            sig = inspect.signature(func)
            params = list(sig.parameters.values())

            # Remove 'client' (first argument)
            new_params = params[1:]
            new_sig = sig.replace(parameters=new_params)

            # Create wrapper with captured func
            def create_wrapper(f):
                @functools.wraps(f)
                def wrapper(*args, **kwargs):
                    client = get_client()
                    return f(client, *args, **kwargs)
                return wrapper

            wrapper = create_wrapper(func)

            wrapper.__signature__ = new_sig

            # Apply documentation override if available
            full_name = f"{module_name}.{func_name}"
            if full_name in overrides:
                wrapper.__doc__ = overrides[full_name]

            mcp.add_tool(wrapper)

            # Expose the wrapper in this module's namespace
            setattr(sys.modules[__name__], func_name, wrapper)

        except Exception as e:
            print(f"Error registering {func_name}: {e}")

# Register tools on import
register_tools()

def start():
    """Entry point to start the MCP server"""
    mcp.run()

if __name__ == "__main__":
    start()
