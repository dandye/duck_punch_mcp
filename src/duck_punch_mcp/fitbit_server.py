
import sys
import os
import inspect
import json
import functools
import typing
import datetime
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Setup paths
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
fitbit_path = os.path.join(project_root, "external/python-fitbit")
if fitbit_path not in sys.path:
    sys.path.insert(0, fitbit_path)

try:
    import fitbit
    from fitbit.api import Fitbit
except ImportError as e:
    sys.stderr.write(f"Error importing fitbit: {e}\n")
    sys.stderr.write(f"Ensure external/python-fitbit is populated\n")
    pass

mcp = FastMCP("Fitbit")

def get_client():
    client_id = os.environ.get("FITBIT_CLIENT_ID")
    client_secret = os.environ.get("FITBIT_CLIENT_SECRET")
    access_token = os.environ.get("FITBIT_ACCESS_TOKEN")
    refresh_token = os.environ.get("FITBIT_REFRESH_TOKEN")

    if not all([client_id, client_secret]):
        # Just return None or raise? Raise is better to signal missing conf
        # But we might want to allow discovery without creds?
        # No, discovery relies on class inspection, not instance.
        # But execution needs creds.
        raise ValueError("FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET are required.")

    def refresh_cb(token):
        sys.stderr.write(f"Refreshed Token: {token}\n")

    return Fitbit(
        client_id,
        client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        refresh_cb=refresh_cb
    )

def create_wrapper(method_name: str, method: Callable, tool_name: str = None,
                   fixed_kwargs: dict = None,
                   remove_params: list = None) -> Callable:
    """
    Creates a wrapper for a Fitbit method.
    fixed_kwargs: arguments to fix (curry)
    remove_params: argument names to remove from signature (besides self)
    """

    if tool_name is None:
        tool_name = method_name

    if remove_params is None:
        remove_params = []

    if fixed_kwargs is None:
        fixed_kwargs = {}

    sig = inspect.signature(method)

    new_params = []
    for name, p in sig.parameters.items():
        if name == 'self':
            continue
        if name in remove_params:
            continue
        if name in fixed_kwargs:
            continue

        # Fitbit SDK doesn't have type hints, so default to Any
        annotation = p.annotation
        if annotation == inspect.Parameter.empty:
            annotation = Any

        # Handle default values
        default = p.default
        if default != inspect.Parameter.empty:
            try:
                json.dumps(default)
            except (TypeError, OverflowError):
                default = None

        new_params.append(p.replace(annotation=annotation, default=default))

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            client = get_client()
            func = getattr(client, method_name)

            # Apply fixed kwargs if the method allows it
            # But wait, standard methods are called directly on client.
            # Dynamic methods are implemented by calling a helper method on client with fixed args.

            # If we are wrapping a standard method (e.g. user_profile_get), func is that method.
            # If we are wrapping a dynamic method (e.g. body), method_name is _COLLECTION_RESOURCE.

            # So we call client.method_name(*args, **kwargs, **fixed_kwargs)

            final_kwargs = kwargs.copy()
            final_kwargs.update(fixed_kwargs)

            result = func(*args, **final_kwargs)

            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2)
            return str(result)
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

    wrapper.__name__ = tool_name

    # Update signature
    new_sig = sig.replace(parameters=new_params, return_annotation=str)
    wrapper.__signature__ = new_sig

    return wrapper

def register_tools():
    try:
        fitbit_class = Fitbit
    except Exception:
        return

    # 1. Standard methods
    for name, method in inspect.getmembers(fitbit_class, predicate=inspect.isfunction):
        if name.startswith("_"): continue

        # Skip internal or utility methods if any

        try:
            wrapper = create_wrapper(name, method)
            mcp.add_tool(wrapper)
        except Exception as e:
            # sys.stderr.write(f"Failed to register {name}: {e}\n")
            pass

    # 2. Dynamic Resource Methods (RESOURCE_LIST)
    # These map to _COLLECTION_RESOURCE(resource=...)
    # defined in __init__: setattr(self, underscore_resource, curry(self._COLLECTION_RESOURCE, resource))

    # We need to wrap _COLLECTION_RESOURCE
    collection_method = getattr(fitbit_class, "_COLLECTION_RESOURCE")

    if hasattr(fitbit_class, "RESOURCE_LIST"):
        for resource in fitbit_class.RESOURCE_LIST:
            underscore_resource = resource.replace('/', '_')

            # Create wrapper for this resource
            # We fix 'resource' arg
            wrapper = create_wrapper(
                method_name="_COLLECTION_RESOURCE",
                method=collection_method,
                tool_name=underscore_resource,
                fixed_kwargs={'resource': resource},
                remove_params=['resource']
            )
            mcp.add_tool(wrapper)

            # Also register delete method if applicable
            # In __init__: if resource not in ['body', 'glucose']: delete_...
            if resource not in ['body', 'glucose']:
                delete_method_name = f"delete_{underscore_resource}"
                delete_impl = getattr(fitbit_class, "_DELETE_COLLECTION_RESOURCE")

                wrapper = create_wrapper(
                    method_name="_DELETE_COLLECTION_RESOURCE",
                    method=delete_impl,
                    tool_name=delete_method_name,
                    fixed_kwargs={'resource': resource},
                    remove_params=['resource']
                )
                mcp.add_tool(wrapper)

    # 3. Dynamic Qualifier Methods (QUALIFIERS)
    # These map to activity_stats(qualifier=...) or _food_stats(qualifier=...)
    # In __init__:
    # setattr(self, '%s_activities' % qualifier, curry(self.activity_stats, qualifier=qualifier))
    # setattr(self, '%s_foods' % qualifier, curry(self._food_stats, qualifier=qualifier))

    activity_stats_method = getattr(fitbit_class, "activity_stats")
    food_stats_method = getattr(fitbit_class, "_food_stats") # This one is protected but we can wrap it

    if hasattr(fitbit_class, "QUALIFIERS"):
        for qualifier in fitbit_class.QUALIFIERS:
            # Activities
            tool_name = f"{qualifier}_activities"
            wrapper = create_wrapper(
                method_name="activity_stats",
                method=activity_stats_method,
                tool_name=tool_name,
                fixed_kwargs={'qualifier': qualifier},
                remove_params=['qualifier']
            )
            mcp.add_tool(wrapper)

            # Foods
            tool_name = f"{qualifier}_foods"
            wrapper = create_wrapper(
                method_name="_food_stats",
                method=food_stats_method,
                tool_name=tool_name,
                fixed_kwargs={'qualifier': qualifier},
                remove_params=['qualifier']
            )
            mcp.add_tool(wrapper)

if __name__ == "__main__":
    register_tools()
    mcp.run()
