
import os
import sys
import json
import logging
import inspect
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP
from googleapiclient.discovery import build
import googleapiclient.discovery
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP
mcp = FastMCP("GoogleAPI")

# Configuration
# Users can specify which APIs to load via environment variable
# GOOGLE_APIS="translate:v2,gmail:v1,calendar:v3"
GOOGLE_APIS_ENV = os.getenv("GOOGLE_APIS", "translate:v2")

def get_target_apis():
    apis = []
    if GOOGLE_APIS_ENV:
        for part in GOOGLE_APIS_ENV.split(","):
            part = part.strip()
            if ":" in part:
                name, version = part.split(":", 1)
                apis.append((name, version))
            else:
                # Default versions if not specified?
                # Better to require it or fetch preferred.
                # For now require it.
                logger.warning(f"Skipping malformed API spec: {part}. Format should be name:version")
    return apis

# Global service cache
_services = {}

def get_service(api_name, api_version):
    key = f"{api_name}:{api_version}"
    if key in _services:
        return _services[key]

    # We rely on ADC or env vars for credentials.
    # `build` uses default credentials if not provided.
    # For introspection during startup we might fail if no creds?
    # Actually `build` fetches discovery doc, which is public usually.
    # But to create the service it might check creds?
    # The discovery doc is public. Creating the service object is cheap.
    # The user might need valid creds to EXECUTE.
    try:
        # We can pass a dummy key for introspection if needed, but we want a real client for execution.
        # So we should let `build` find creds.
        # But if we are just exploring structure, we might fallback?
        service = build(api_name, api_version)
        _services[key] = service
        return service
    except Exception as e:
        logger.error(f"Failed to build service {key}: {e}")
        # Fallback with dummy key for introspection purposes ONLY?
        # No, because then the wrapper will use that client and fail on execution.
        # We assume the environment is set up correctly (ADC).
        raise e

def create_tool_wrapper(service_factory, resource_path, method_name, tool_name, method_desc):
    """
    Creates a wrapper function for an API method.

    service_factory: callable returning the (service, resource_object)
    resource_path: list of resource names leading to this method
    method_name: name of the method
    tool_name: name exposed to MCP
    method_desc: dict from discovery doc describing the method (parameters, etc)
    """

    parameters = method_desc.get('parameters', {})
    doc = method_desc.get('description', '')

    # Construct signature
    # We need to map discovery parameters to function parameters
    # The parameters dict contains:
    # "q": { "type": "string", "description": "...", "required": true, "location": "query" }

    # We will accept **kwargs to catch everything, but for better LLM performance
    # we should explicitly list parameters.

    sig_params = []
    annotations = {}

    # Helper to map JSON schema types to Python types
    type_map = {
        'string': str,
        'integer': int,
        'boolean': bool,
        'number': float,
        'array': list,
        'object': dict
    }

    for param_name, param_info in parameters.items():
        p_type_str = param_info.get('type', 'string')
        p_type = type_map.get(p_type_str, str)

        required = param_info.get('required', False)
        default = inspect.Parameter.empty if required else None

        # Determine kind. Most are keyword arguments.
        kind = inspect.Parameter.KEYWORD_ONLY

        # We can make required ones POSITIONAL_OR_KEYWORD if we want,
        # but KEYWORD_ONLY is safer for many params.

        sig_params.append(
            inspect.Parameter(
                name=param_name,
                kind=kind,
                default=default,
                annotation=p_type
            )
        )
        annotations[param_name] = p_type

    # If the method supports a request body (e.g. POST), it usually has a 'body' parameter in Python client
    # or takes a dict. The discovery doc says "request": { "$ref": "..." }
    if 'request' in method_desc:
        # It expects a body.
        # In python client, this is usually passed as `body` argument.
        sig_params.append(
            inspect.Parameter(
                name='body',
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=dict
            )
        )
        annotations['body'] = dict
        doc += "\n\n:param body: The request body as a JSON object."

    # Return annotation
    annotations['return'] = str

    # Define wrapper
    def wrapper(**kwargs):
        try:
            # Get resource
            resource = service_factory()

            # The method on the resource
            func = getattr(resource, method_name)

            # Call it
            request = func(**kwargs)

            # Execute
            response = request.execute()

            return json.dumps(response, indent=2)
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    wrapper.__name__ = tool_name
    wrapper.__doc__ = doc
    wrapper.__signature__ = inspect.Signature(
        parameters=sig_params,
        return_annotation=str
    )
    wrapper.__annotations__ = annotations

    return wrapper

def register_tools_for_api(api_name, api_version):
    try:
        # For introspection, we need a service object.
        # If credentials are missing, this might fail.
        # Strategy: Use a dummy key for introspection phase if real build fails?
        # But we need the real service factory for the tool.
        # Let's try to build with dummy key for *discovery*, but use `get_service` (which tries real creds) for execution.

        introspection_service = build(api_name, api_version, developerKey="AIzaDummyKeyForIntrospection")

        if not hasattr(introspection_service, '_resourceDesc'):
             logger.warning(f"No _resourceDesc found for {api_name} {api_version}")
             return

        rd = introspection_service._resourceDesc

        def process_resource(resource_desc, path_prefix, resource_accessor_factory):
            """
            resource_desc: dict of resource description
            path_prefix: string prefix for tool name (e.g. Translate_detections)
            resource_accessor_factory: function that returns the resource object from the service
            """

            # Methods on this resource
            methods = resource_desc.get('methods', {})
            for m_name, m_desc in methods.items():
                tool_name = f"{path_prefix}_{m_name}"

                # Factory that gets the correct resource
                # We need to capture the current resource_accessor_factory
                factory = resource_accessor_factory

                mcp.add_tool(create_tool_wrapper(
                    factory,
                    path_prefix,
                    m_name,
                    tool_name,
                    m_desc
                ))

            # Sub-resources
            resources = resource_desc.get('resources', {})
            for r_name, r_desc in resources.items():
                new_prefix = f"{path_prefix}_{r_name}"

                # New factory: gets parent resource, then calls sub-resource method
                # e.g. service.translations()
                # But wait, `translations` is a method on service.

                def make_sub_factory(parent_factory, res_name):
                    return lambda: getattr(parent_factory(), res_name)()

                sub_factory = make_sub_factory(resource_accessor_factory, r_name)

                process_resource(r_desc, new_prefix, sub_factory)

        # Top level methods
        # Prefix: TitleCase(api_name) e.g. Translate
        prefix = api_name.title()

        # Factory for top level service
        def service_factory():
            return get_service(api_name, api_version)

        process_resource(rd, prefix, service_factory)

        logger.info(f"Registered tools for {api_name} {api_version}")

    except Exception as e:
        logger.error(f"Error registering {api_name} {api_version}: {e}")

def main():
    targets = get_target_apis()
    if not targets:
        logger.warning("No APIs configured. Set GOOGLE_APIS env var (e.g. 'translate:v2').")

    for name, version in targets:
        register_tools_for_api(name, version)

    mcp.run()

if __name__ == "__main__":
    main()
