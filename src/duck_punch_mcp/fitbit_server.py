
import os
import importlib
import inspect
import functools
import sys
import json
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Import Fitbit library
try:
    import fitbit
    from fitbit import Fitbit
except ImportError:
    # This will be handled by the check in imports or at runtime
    pass

load_dotenv()

# Initialize FastMCP
mcp = FastMCP("Fitbit")

# Global client instance
_client = None

def get_client() -> Fitbit:
    global _client
    if _client is None:
        client_id = os.environ.get("FITBIT_CLIENT_ID")
        client_secret = os.environ.get("FITBIT_CLIENT_SECRET")
        access_token = os.environ.get("FITBIT_ACCESS_TOKEN")
        refresh_token = os.environ.get("FITBIT_REFRESH_TOKEN")
        expires_at = os.environ.get("FITBIT_EXPIRES_AT")

        if not all([client_id, client_secret, access_token, refresh_token]):
             # We allow starting without creds for inspection, but tools will fail
             return None

        def refresh_cb(token):
            """
            Callback for token refresh.
            IMPORTANT: Do not log sensitive tokens to stdout/stderr.
            In a real app, you would save these to a DB or file.
            """
            # Implementation specific: just update the env vars in memory or similar?
            # For this MCP server, we might just print a message that token refreshed (without the token)
            # or try to write back to .env? Writing back to .env is risky/complex.
            # We'll just update our in-memory understanding if needed, but requests-oauthlib handles the session.
            # The memory explicitly says: "prevent logging sensitive tokens".
            print("Fitbit access token refreshed.", file=sys.stderr)

        _client = Fitbit(
            client_id,
            client_secret,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=float(expires_at) if expires_at else None,
            refresh_cb=refresh_cb
        )

    return _client


# Explicit wrappers for curried/dynamic methods
# Based on inspection, these map to time_series(resource, ...)
# Signature of time_series: (self, resource, user_id=None, base_date='today', period=None, end_date=None)

def sleep(base_date: str = 'today', user_id: str = None, period: str = None, end_date: str = None) -> str:
    """
    Get sleep data.

    Args:
        base_date: The date of records to be returned. In the format 'yyyy-MM-dd' or 'today'.
        user_id: The encoded ID of the user. Use '-' (dash) for current logged-in user.
        period: The range for which data will be returned. Options: 1d, 7d, 30d, 1w, 1m, 3m, 6m, 1y, max.
        end_date: The end date of the range.
    """
    client = get_client()
    if not client:
        return "Error: Fitbit client not initialized. Check environment variables."
    # client.sleep is curried time_series('sleep', ...)
    return str(client.sleep(user_id=user_id, base_date=base_date, period=period, end_date=end_date))

def activities(base_date: str = 'today', user_id: str = None, period: str = None, end_date: str = None, **kwargs) -> str:
    """
    Get activities data.

    Args:
        base_date: The date of records to be returned. In the format 'yyyy-MM-dd' or 'today'.
        user_id: The encoded ID of the user. Use '-' (dash) for current logged-in user.
        period: The range for which data will be returned. Options: 1d, 7d, 30d, 1w, 1m, 3m, 6m, 1y, max.
        end_date: The end date of the range.
    """
    # Handle nested kwargs injection from LLMs
    if 'kwargs' in kwargs:
        inner_kwargs = kwargs.pop('kwargs')
        if isinstance(inner_kwargs, dict):
            kwargs.update(inner_kwargs)

    # Handle common hallucination: 'date' instead of 'base_date'
    if base_date == 'today' and 'date' in kwargs:
        base_date = kwargs.pop('date')

    client = get_client()
    if not client:
        return "Error: Fitbit client not initialized. Check environment variables."
    return str(client.activities(user_id=user_id, base_date=base_date, period=period, end_date=end_date))

def body(base_date: str = 'today', user_id: str = None, period: str = None, end_date: str = None) -> str:
    """
    Get body data (weight, bmi, fat).
    """
    client = get_client()
    if not client:
        return "Error: Fitbit client not initialized. Check environment variables."
    return str(client.body(user_id=user_id, base_date=base_date, period=period, end_date=end_date))

def heart(base_date: str = 'today', user_id: str = None, period: str = None, end_date: str = None) -> str:
    """
    Get heart rate data.
    """
    client = get_client()
    if not client:
        return "Error: Fitbit client not initialized. Check environment variables."
    return str(client.heart(user_id=user_id, base_date=base_date, period=period, end_date=end_date))

def bp(base_date: str = 'today', user_id: str = None, period: str = None, end_date: str = None) -> str:
    """
    Get blood pressure data.
    """
    client = get_client()
    if not client:
        return "Error: Fitbit client not initialized. Check environment variables."
    return str(client.bp(user_id=user_id, base_date=base_date, period=period, end_date=end_date))

def register_tools():
    # Register explicit wrappers
    mcp.add_tool(sleep)
    mcp.add_tool(activities)
    mcp.add_tool(body)
    mcp.add_tool(heart)
    mcp.add_tool(bp)

    # Discovery of other methods
    # We will inspect the Fitbit class and register methods that look like tools
    # excluding the ones we already registered or are problematic

    # List of explicit wrappers names
    explicit_names = ['sleep', 'activities', 'body', 'heart', 'bp']

    for name, method in inspect.getmembers(Fitbit):
        if name.startswith("_"):
            continue

        if name in explicit_names:
            continue

        if not inspect.isfunction(method):
            continue

        # Skip methods that are just constants or properties (though isfunction checks this mostly)

        try:
            sig = inspect.signature(method)
            # Most methods take 'self' as first arg, which we need to handle by injecting client

            # Create a wrapper
            # We need to act carefully about signature
            # 'self' is the first parameter

            params = list(sig.parameters.values())
            if not params or params[0].name != 'self':
                # Unexpected signature for an instance method?
                # Actually, inspect.getmembers(Fitbit) returns unbound functions
                pass

            # Prepare new signature (remove 'self')
            new_params = params[1:]
            new_sig = sig.replace(parameters=new_params)

            # Create wrapper
            # We use a closure to capture the method name/func
            def create_wrapper(func, func_name):
                @functools.wraps(func)
                def wrapper(*args, **kwargs):
                    client = get_client()
                    if not client:
                        return "Error: Fitbit client not initialized."

                    # Handle nested kwargs injection from LLMs
                    if 'kwargs' in kwargs:
                        inner_kwargs = kwargs.pop('kwargs')
                        if isinstance(inner_kwargs, dict):
                            kwargs.update(inner_kwargs)

                    # We invoke the method on the client instance
                    # Since 'func' is unbound class method, we can call it with (client, *args)
                    # OR we can just get the method from the client instance
                    bound_method = getattr(client, func_name)
                    return str(bound_method(*args, **kwargs))

                return wrapper

            wrapper = create_wrapper(method, name)
            wrapper.__signature__ = new_sig

            mcp.add_tool(wrapper)

        except ValueError:
            # Could not get signature
            continue
        except Exception as e:
            print(f"Failed to register tool {name}: {e}")

# Register tools on import
register_tools()

if __name__ == "__main__":
    mcp.run()
