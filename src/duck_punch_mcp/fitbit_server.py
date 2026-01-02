from typing import Any, Dict, Optional
import os
import inspect
import fitbit
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Initialize the MCP server
mcp = FastMCP("fitbit")

# Initialize Fitbit client
client_id = os.getenv("FITBIT_CLIENT_ID")
client_secret = os.getenv("FITBIT_CLIENT_SECRET")
access_token = os.getenv("FITBIT_ACCESS_TOKEN")
refresh_token = os.getenv("FITBIT_REFRESH_TOKEN")
expires_at = os.getenv("FITBIT_EXPIRES_AT")

if expires_at:
    try:
        expires_at = float(expires_at)
    except ValueError:
        expires_at = None

client = None
if client_id and client_secret and access_token and refresh_token:
    client = fitbit.Fitbit(
        client_id,
        client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        refresh_cb=lambda token: print("Token refreshed")
    )

def register_tools():
    """
    Dynamically registers tools from the Fitbit SDK.
    Iterates over RESOURCE_LIST and QUALIFIERS to find potential methods.
    """
    if not client:
        print("Fitbit client not initialized. Tools will not be registered.")
        return

    # Helper function to create a wrapper for the Fitbit method
    def create_wrapper(method_name, method):
        # Explicit wrappers for key methods to ensure correct schema generation
        if method_name == 'sleep':
            def sleep(date: str, user_id: Optional[str] = None) -> str:
                """
                Get sleep data for a specific date.

                Args:
                    date: The date to get sleep data for (format: YYYY-MM-DD)
                    user_id: The user ID (defaults to current user)
                """
                try:
                    kwargs = {'date': date}
                    if user_id:
                        kwargs['user_id'] = user_id
                    result = method(**kwargs)
                    return str(result)
                except Exception as e:
                    return f"Error executing {method_name}: {str(e)}"
            return sleep

        elif method_name == 'activities_daily':
             def activities_daily(date: str, user_id: Optional[str] = None) -> str:
                """
                Get daily activity summary for a specific date.

                Args:
                    date: The date to get activity data for (format: YYYY-MM-DD)
                    user_id: The user ID (defaults to current user)
                """
                try:
                    kwargs = {'date': date}
                    if user_id:
                        kwargs['user_id'] = user_id
                    result = method(**kwargs)
                    return str(result)
                except Exception as e:
                    return f"Error executing {method_name}: {str(e)}"
             return activities_daily

        elif method_name == 'heart':
             def heart(date: str, user_id: Optional[str] = None) -> str:
                """
                Get heart rate data for a specific date.

                Args:
                    date: The date to get heart rate data for (format: YYYY-MM-DD)
                    user_id: The user ID (defaults to current user)
                """
                try:
                    kwargs = {'date': date}
                    if user_id:
                        kwargs['user_id'] = user_id
                    result = method(**kwargs)
                    return str(result)
                except Exception as e:
                    return f"Error executing {method_name}: {str(e)}"
             return heart

        elif method_name == 'bp':
             def bp(date: str, user_id: Optional[str] = None) -> str:
                """
                Get blood pressure data for a specific date.

                Args:
                    date: The date to get BP data for (format: YYYY-MM-DD)
                    user_id: The user ID (defaults to current user)
                """
                try:
                    kwargs = {'date': date}
                    if user_id:
                        kwargs['user_id'] = user_id
                    result = method(**kwargs)
                    return str(result)
                except Exception as e:
                    return f"Error executing {method_name}: {str(e)}"
             return bp

        elif method_name == 'user_profile_get':
             def user_profile_get(user_id: Optional[str] = None) -> str:
                """
                Get user profile.

                Args:
                    user_id: The user ID (defaults to current user)
                """
                try:
                    kwargs = {}
                    if user_id:
                        kwargs['user_id'] = user_id
                    result = method(**kwargs)
                    return str(result)
                except Exception as e:
                    return f"Error executing {method_name}: {str(e)}"
             return user_profile_get

        # Fallback for other methods
        def wrapper(**kwargs: Any) -> str:
            """
            Dynamically wrapped Fitbit API method.
            Use this if no specific tool is available.
            Arguments are passed directly to the SDK method.
            Common args: date (YYYY-MM-DD), user_id, base_date, period.
            """
            try:
                result = method(**kwargs)
                return str(result)
            except Exception as e:
                return f"Error executing {method_name}: {str(e)}"

        return wrapper

    # Iterate through resources and qualifiers
    # Fitbit SDK uses `__getattr__` or `__init__` magic with these lists to create methods.
    # Typically methods are like `client.sleep()`, `client.activities_daily()`, etc.
    # Or sometimes `client.time_series('activities/steps', ...)`

    # Based on memory: "requires iterating over Fitbit.RESOURCE_LIST and Fitbit.QUALIFIERS"

    # Let's see what methods are actually available on the client instance.
    # The python-fitbit library dynamically adds methods.

    # We will try to construct method names based on resources and check if they exist.

    resources = getattr(fitbit.Fitbit, 'RESOURCE_LIST', [])
    qualifiers = getattr(fitbit.Fitbit, 'QUALIFIERS', [])

    # Also standard methods that might be useful
    standard_methods = [
        'user_profile_get', 'activities_daily', 'activities_weekly',
        'sleep', 'heart', 'bp', 'activities', 'food_logs', 'water_logs',
        'body_fat_logs', 'body_weight_logs'
    ]

    # Combine potential method names
    potential_methods = set(standard_methods)

    # Add resources as potential methods (e.g. 'sleep' is in RESOURCE_LIST)
    for resource in resources:
        potential_methods.add(resource)

    # Check what exists on the client
    for method_name in potential_methods:
        if hasattr(client, method_name):
            method = getattr(client, method_name)
            if callable(method):
                tool_name = method_name.replace("_", "-")
                wrapper = create_wrapper(method_name, method)

                # Register the tool
                # Note: We use the function name from the wrapper if available to help introspection
                mcp.tool(name=tool_name)(wrapper)
                print(f"Registered tool: {tool_name}")

# Register tools at module level so they are available on import
register_tools()

if __name__ == "__main__":
    mcp.run()
