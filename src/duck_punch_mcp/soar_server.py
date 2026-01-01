
import sys
import os
import inspect
import json
import functools
import typing
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Add external submodules to path for soar-sdk
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
soar_path = os.path.join(project_root, "external/soar-sdk/src")

# Add exact package dir for legacy peer imports
soar_package_path = os.path.join(soar_path, "soar_sdk")
if soar_package_path not in sys.path:
    sys.path.insert(0, soar_package_path)

try:
    import Siemplify
    import SiemplifyUtils
    # Restore stdout immediately because SiemplifyUtils overrides it on import.
    SiemplifyUtils.resume_stdout()
except ImportError as e:
    sys.stderr.write(f"Error importing soar_sdk (Siemplify): {e}\n")
    sys.stderr.write(f"Ensure external/soar-sdk is populated (git submodule update --init --recursive)\n")
    # For now we might choke here if SDK is missing
    pass

# Initialize FastMCP
mcp = FastMCP("soar-sdk")

def get_client():
    """Creates an authenticated Siemplify client instance."""
    # Retrieve API key from environment variable
    api_key = os.environ.get("SIEMPLIFY_API_KEY", "DUMMY_KEY")

    # Mock sys.argv as SiemplifyBase expects sys.argv[1] to be the API key
    original_argv = sys.argv
    # We must ensure argv has at least 2 arguments.
    # The first one is script name, second is API key.
    sys.argv = ["mcp_server", api_key]

    try:
        # Siemplify instantiation might fail if other env vars are missing.
        # But we assume the environment is set up correctly by the user.
        client = Siemplify.Siemplify()
        return client
    except Exception as e:
        sys.stderr.write(f"Error instantiating Siemplify client: {e}\n")
        raise
    finally:
        sys.argv = original_argv

def is_simple_type(t):
    """Checks if a type is a simple type supported by MCP schema directly."""
    if t in (str, int, float, bool, list, dict, type(None)):
        return True

    # Handle Generic Alias (like list[str]) or Optional (Union[..., None])
    try:
        origin = typing.get_origin(t)
        if origin is typing.Union:
            args = typing.get_args(t)
            return all(is_simple_type(a) for a in args)
        if origin in (list, dict):
            return True
    except Exception:
        pass

    return False

def create_wrapper(method_name: str, method: Callable) -> Callable:
    """Creates a wrapper function for a Siemplify method."""

    sig = inspect.signature(method)

    # Analyze parameters to map renamed ones (like '_') and fix types
    param_mapping = {} # new_name -> old_name
    new_params = []

    for name, p in sig.parameters.items():
        if name == 'self':
            continue

        new_name = name
        if name == '_':
            new_name = 'unused_param'

        if new_name != name:
            param_mapping[new_name] = name

        # Fix type annotation
        annotation = p.annotation
        if annotation != inspect.Parameter.empty:
            if not is_simple_type(annotation):
                # If complex type, use Any
                annotation = Any

        # Handle default values
        default = p.default
        if default != inspect.Parameter.empty:
            try:
                json.dumps(default)
            except (TypeError, OverflowError):
                default = None

        # If default is empty but we converted type to Any, it might still be fine.

        new_p = p.replace(name=new_name, annotation=annotation, default=default)
        new_params.append(new_p)

    # Create the wrapper function
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        # Remap kwargs if needed
        for new_name, old_name in param_mapping.items():
            if new_name in kwargs:
                kwargs[old_name] = kwargs.pop(new_name)

        try:
            client = get_client()
            func = getattr(client, method_name)
            result = func(*args, **kwargs)

            # Format result for MCP
            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2)
            return str(result)
        except Exception as e:
            return f"Error executing {method_name}: {str(e)}"

    # Modify the signature
    # Return type is always string (or Any) to avoid complex return types
    new_sig = sig.replace(parameters=new_params, return_annotation=str)
    wrapper.__signature__ = new_sig

    return wrapper

def register_tools():
    """Dynamically discovers and registers Siemplify methods as MCP tools."""
    try:
        siemplify_class = Siemplify.Siemplify
    except Exception:
        # If import failed earlier, we can't register tools
        return

    # Iterate over all members of the class
    for name, method in inspect.getmembers(siemplify_class, predicate=inspect.isfunction):
        # Skip private methods and __init__
        if name.startswith("_"):
            continue

        # Skip obvious non-API methods or signal handlers
        if name in ['termination_signal_handler', 'cancellation_signal_handler', 'create_session']:
            continue

        # Create wrapper
        try:
            wrapper = create_wrapper(name, method)
            # Register with FastMCP
            mcp.add_tool(wrapper)
        except Exception as e:
            # sys.stderr.write(f"Warning: Failed to register tool {name}: {e}\n")
            pass

def start():
    """Entry point to start the MCP server"""
    # Only register tools if not already running (FastMCP might handle this, but good to be explicit for dynamic registration)
    register_tools()
    mcp.run()

if __name__ == "__main__":
    start()
