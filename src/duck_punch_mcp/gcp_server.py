
import os
import sys
import inspect
import pkgutil
import importlib
import json
import functools
import re
import typing
from typing import Any, Callable, Union, Optional, List, Dict

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Setup paths
# We are in src/duck_punch_mcp/gcp_server.py, so root is two levels up
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
gcp_packages_root = os.path.join(project_root, "external/google-cloud-python/packages")

# Whitelist of packages to expose
TARGET_PACKAGES = [
    "google-cloud-access-approval",
    "google-cloud-asset",
    "google-cloud-advisorynotifications",
    "google-cloud-alloydb",
    "google-cloud-api-gateway",
    "google-cloud-api-keys",
    "google-cloud-apigee-connect",
    "google-cloud-apigee-registry",
    "google-cloud-apihub",
    "google-cloud-appengine-admin",
    "google-cloud-apphub",
    "google-cloud-artifact-registry",
    "google-cloud-assured-workloads",
    "google-cloud-automl",
]

# Add packages to sys.path
# We need to add the individual package directories because they are namespace packages
for pkg_name in TARGET_PACKAGES:
    pkg_path = os.path.join(gcp_packages_root, pkg_name)
    if os.path.exists(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)

mcp = FastMCP("GoogleCloud")

# Global cache for clients
_clients = {}

def get_client(module_name: str, client_class_name: str):
    """Lazy loads and caches a GCP client."""
    key = f"{module_name}.{client_class_name}"
    if key in _clients:
        return _clients[key]

    try:
        module = importlib.import_module(module_name)
        client_cls = getattr(module, client_class_name)
        # Instantiate client. This assumes ADC (Application Default Credentials) work.
        # or GOOGLE_APPLICATION_CREDENTIALS is set.
        client = client_cls()
        _clients[key] = client
        return client
    except Exception as e:
        sys.stderr.write(f"Error instantiating {client_class_name}: {e}\n")
        raise

def is_simple_type(t):
    """Checks if a type is simple enough for MCP direct mapping."""
    if t in (str, int, float, bool, list, dict, type(None)):
        return True

    # Check for typing.Optional[Simple]
    origin = typing.get_origin(t)
    if origin is Union:
        args = typing.get_args(t)
        return all(is_simple_type(a) for a in args)

    return False

def create_wrapper(client_factory: Callable, method_name: str, method: Callable, tool_name: str):
    """Creates a wrapper for a GCP SDK method."""

    sig = inspect.signature(method)

    # Filter parameters
    new_params = []

    for name, p in sig.parameters.items():
        if name == 'self':
            continue

        # Determine annotation
        annotation = p.annotation

        # If no annotation, assume Any
        if annotation == inspect.Parameter.empty:
            annotation = Any

        # If it's not a simple type, force it to Dict (JSON object)
        # Using Any seems to cause Pydantic to try to introspect the original type sometimes?
        if not is_simple_type(annotation):
            # Fallback to dict for complex types (Protos usually serialize to dict)
            # or Any if we want to be safe. But let's try dict to break the Pydantic chain.
            annotation = dict

        # Handle default values
        # If default is not simple/JSON serializable, set to None
        default = p.default
        if default != inspect.Parameter.empty:
            try:
                # Basic check if it is serializable
                json.dumps(default)
            except (TypeError, OverflowError):
                default = None

        new_params.append(p.replace(annotation=annotation, default=default))

    # Do not use functools.wraps to avoid leaking original annotations/signature via __wrapped__
    def wrapper(*args, **kwargs):
        try:
            client = client_factory()
            func = getattr(client, method_name)

            # If the first argument is 'request' and it's a dict, the SDK usually handles it
            # if we pass it as a request object or keywords.
            # Let's just pass through.

            result = func(*args, **kwargs)

            # Result is often a Pager or a Proto message.
            # We need to serialize it.

            # Check if it's a Pager
            if hasattr(result, "pages"):
                # It's likely a pager. Let's return the first page or a list of items (limited)
                # For safety, let's convert to list of dicts (limited to first 20 items to avoid blowing up)
                items = []
                try:
                    for i, item in enumerate(result):
                        if i >= 20:
                            break
                        # ProtoMessage to dict
                        if hasattr(item, "__class__") and hasattr(item.__class__, "to_json"):
                             items.append(json.loads(item.__class__.to_json(item)))
                        elif hasattr(item, "__dict__"):
                             items.append(str(item)) # Fallback
                        else:
                             items.append(str(item))
                    return json.dumps(items, indent=2)
                except Exception as e:
                    return f"Error iterating pager: {e}"

            # Check if it's a Proto Message
            if hasattr(result, "__class__") and hasattr(result.__class__, "to_json"):
                return result.__class__.to_json(result)

            # Basic types
            if isinstance(result, (dict, list, str, int, float, bool, type(None))):
                if isinstance(result, (dict, list)):
                    return json.dumps(result, indent=2)
                return str(result)

            return str(result)

        except Exception as e:
            return f"Error executing {method_name}: {str(e)}"

    # Manually set attributes
    wrapper.__name__ = tool_name
    wrapper.__doc__ = method.__doc__

    # Update signature
    # Replace parameters AND return annotation
    new_sig = sig.replace(parameters=new_params, return_annotation=str)
    wrapper.__signature__ = new_sig

    # Explicitly set annotations based on new parameters
    wrapper.__annotations__ = {
        p.name: p.annotation for p in new_params
    }
    wrapper.__annotations__['return'] = str

    return wrapper

def discover_tools():
    """Discover tools from whitelisted packages."""

    # Manual mapping of expected modules for the whitelisted packages
    # We could try to walk the directories, but let's be explicit for the 'first 2'

    candidates = [
        # (package_dir_name, module_to_import, expected_client_name)
        ("google-cloud-access-approval", "google.cloud.accessapproval", "AccessApprovalClient"),
        ("google-cloud-asset", "google.cloud.asset_v1", "AssetServiceClient"),
        ("google-cloud-advisorynotifications", "google.cloud.advisorynotifications_v1", "AdvisoryNotificationsServiceClient"),
        ("google-cloud-alloydb", "google.cloud.alloydb_v1", "AlloyDBAdminClient"),

        # New 10 packages
        ("google-cloud-api-gateway", "google.cloud.apigateway_v1", "ApiGatewayServiceClient"),
        ("google-cloud-api-keys", "google.cloud.api_keys_v2", "ApiKeysClient"),
        ("google-cloud-apigee-connect", "google.cloud.apigeeconnect_v1", "ConnectionServiceClient"),
        ("google-cloud-apigee-registry", "google.cloud.apigee_registry_v1", "RegistryClient"),
        ("google-cloud-apihub", "google.cloud.apihub_v1", "ApiHubClient"),
        ("google-cloud-appengine-admin", "google.cloud.appengine_admin_v1", "ApplicationsClient"),
        ("google-cloud-apphub", "google.cloud.apphub_v1", "AppHubClient"),
        ("google-cloud-artifact-registry", "google.cloud.artifactregistry_v1", "ArtifactRegistryClient"),
        ("google-cloud-assured-workloads", "google.cloud.assuredworkloads_v1", "AssuredWorkloadsServiceClient"),
        ("google-cloud-automl", "google.cloud.automl_v1", "AutoMlClient"),
    ]

    for pkg_name, module_name, client_name in candidates:
        try:
            mod = importlib.import_module(module_name)

            # Find the client class
            # We accept exact match or something ending with the name
            client_cls = getattr(mod, client_name, None)
            if not client_cls:
                sys.stderr.write(f"Could not find {client_name} in {module_name}\n")
                continue

            print(f"Registering tools for {client_name}...")

            # Factory to get instance
            def make_factory(m_name, c_name):
                return lambda: get_client(m_name, c_name)

            client_factory = make_factory(module_name, client_name)

            # Iterate over methods
            for name, method in inspect.getmembers(client_cls):
                if name.startswith("_"): continue
                if not inspect.isfunction(method): continue

                # Filter out some common non-API methods
                if name in ["from_service_account_file", "from_service_account_info", "from_service_account_json", "get_mtls_endpoint_and_cert_source", "parse_common_billing_account_path", "parse_common_folder_path", "parse_common_location_path", "parse_common_organization_path", "parse_common_project_path", "common_billing_account_path", "common_folder_path", "common_location_path", "common_organization_path", "common_project_path"]:
                    continue

                # Tool name: <Service>_<Method>
                # e.g. AccessApproval_list_approval_requests
                service_prefix = client_name.replace("Client", "").replace("Service", "")
                tool_name = f"{service_prefix}_{name}"

                try:
                    wrapper = create_wrapper(client_factory, name, method, tool_name)
                    mcp.add_tool(wrapper)
                except Exception as e:
                    sys.stderr.write(f"Failed to wrap {name}: {e}\n")

        except ImportError as e:
            sys.stderr.write(f"Failed to import {module_name}: {e}\n")

if __name__ == "__main__":
    discover_tools()
    mcp.run()
