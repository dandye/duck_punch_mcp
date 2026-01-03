
import os
import sys
import json
import logging
import inspect
import concurrent.futures
import hashlib
import re
import requests
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP
from googleapiclient.discovery import build
import googleapiclient.discovery
import google.auth
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP
mcp = FastMCP("GoogleAPI")

# Configuration
# Users can specify which APIs to load via environment variable
# GOOGLE_APIS="translate:v2,gmail:v1"
# If not set, or set to "ALL", we discover all preferred APIs.
GOOGLE_APIS_ENV = os.getenv("GOOGLE_APIS", "ALL")

def get_all_apis():
    """Fetch all preferred APIs from the Google Discovery Directory."""
    try:
        url = "https://www.googleapis.com/discovery/v1/apis"
        logger.info(f"Fetching API list from {url}...")
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()

        apis = []
        for item in data.get('items', []):
            if item.get('preferred', False):
                apis.append((item['name'], item['version']))

        logger.info(f"Discovered {len(apis)} preferred APIs.")
        return apis
    except Exception as e:
        logger.error(f"Failed to fetch API list: {e}")
        return []

def get_target_apis():
    if GOOGLE_APIS_ENV == "ALL":
        return get_all_apis()

    apis = []
    if GOOGLE_APIS_ENV:
        for part in GOOGLE_APIS_ENV.split(","):
            part = part.strip()
            if not part: continue
            if ":" in part:
                name, version = part.split(":", 1)
                apis.append((name, version))
            else:
                logger.warning(f"Skipping malformed API spec: {part}. Format should be name:version")

    if not apis:
         # Fallback to ALL if empty string provided? Or maybe just return empty list.
         # If explictly empty string, maybe they want nothing?
         # But usually empty env var means default.
         # If GOOGLE_APIS_ENV was actually empty string (not None), we might want default.
         if not GOOGLE_APIS_ENV: # None or empty
             return get_all_apis()

    return apis

# Global service cache
_services = {}

# Global tool registry for listing
_registered_tools = []

# Global registry for API scopes
# API Name -> List of Scopes
_api_scopes = {}

def get_service(api_name, api_version, scopes=None):
    key = f"{api_name}:{api_version}"
    if key in _services:
        return _services[key]

    try:
        # Explicitly load credentials with scopes if provided
        # This ensures we request the correct permissions from ADC
        if scopes:
            credentials, _ = google.auth.default(scopes=scopes)
            service = build(api_name, api_version, credentials=credentials)
        else:
            service = build(api_name, api_version)

        _services[key] = service
        return service
    except Exception as e:
        logger.error(f"Failed to build service {key}: {e}")
        raise e

def sanitize_tool_name(name: str) -> str:
    """
    Sanitizes a tool name to be a valid MCP tool name (max 64 chars, alphanumeric, _, -, ., :).
    """
    # Replace invalid chars with _
    sanitized = re.sub(r'[^a-zA-Z0-9_.:-]', '_', name)

    # Ensure it starts with a letter or underscore
    if not sanitized[0].isalpha() and sanitized[0] != '_':
        sanitized = 'A' + sanitized

    if len(sanitized) <= 64:
        return sanitized

    # If too long, keep prefix and suffix, and hash the middle
    # We want to preserve readability of the start (API/Resource) and end (Method)
    # 64 chars limit.
    # Keep 25 chars prefix, 8 chars hash, 30 chars suffix? = 63.

    # e.g. Accesscontextmanager_accessPolicies_accessLevels_testIamPermissions (71)
    # Prefix: Accesscontextmanager_acce (25)
    # Suffix: Levels_testIamPermissions (30)
    # Hash of middle: ...

    # A simple deterministic strategy:
    # Hash the full name -> 8 chars (hex is 2 chars per byte, so 4 bytes)
    # Take first 55 chars + '_' + 8 char hash?
    # Or just hash the whole thing if it's too long? No, readability matters.

    # Strategy:
    # 1. Take first 30 chars.
    # 2. Take last 25 chars.
    # 3. Middle 8 chars hash of the *entire* string (to avoid collision if start/end matches).

    h = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
    prefix = sanitized[:27]
    suffix = sanitized[-27:]

    return f"{prefix}_{h}_{suffix}"

def create_tool_wrapper(service_factory, resource_path, method_name, tool_name, method_desc):
    """
    Creates a wrapper function for an API method.
    """
    tool_name = sanitize_tool_name(tool_name)

    parameters = method_desc.get('parameters', {})
    doc = method_desc.get('description', '')

    sig_params = []
    annotations = {}

    type_map = {
        'string': str,
        'integer': int,
        'boolean': bool,
        'number': float,
        'array': list,
        'object': dict
    }

    # Mapping from sanitized name to original name
    param_mapping = {}

    for param_name, param_info in parameters.items():
        p_type_str = param_info.get('type', 'string')
        p_type = type_map.get(p_type_str, str)

        required = param_info.get('required', False)
        default = inspect.Parameter.empty if required else None

        kind = inspect.Parameter.KEYWORD_ONLY

        # Sanitize parameter name (replace . with _)
        sanitized_param_name = param_name.replace('.', '_')
        param_mapping[sanitized_param_name] = param_name

        sig_params.append(
            inspect.Parameter(
                name=sanitized_param_name,
                kind=kind,
                default=default,
                annotation=p_type
            )
        )
        annotations[sanitized_param_name] = p_type

    if 'request' in method_desc:
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

    annotations['return'] = str

    def wrapper(**kwargs):
        try:
            resource = service_factory()
            func = getattr(resource, method_name)

            # Map kwargs back to original parameter names
            api_kwargs = {}
            for k, v in kwargs.items():
                if k == 'body':
                    api_kwargs['body'] = v
                elif k in param_mapping:
                    api_kwargs[param_mapping[k]] = v
                else:
                    # Fallback
                    api_kwargs[k] = v

            request = func(**api_kwargs)
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
        # Use dummy key for introspection to avoid startup auth issues
        introspection_service = build(api_name, api_version, developerKey="AIzaDummyKeyForIntrospection")

        if not hasattr(introspection_service, '_resourceDesc'):
             logger.warning(f"No _resourceDesc found for {api_name} {api_version}")
             return

        rd = introspection_service._resourceDesc

        # Extract scopes
        auth_info = rd.get('auth', {}).get('oauth2', {}).get('scopes', {})
        scopes = list(auth_info.keys())

        # Register scopes
        _api_scopes[f"{api_name}:{api_version}"] = scopes

        def process_resource(resource_desc, path_prefix, resource_accessor_factory):
            methods = resource_desc.get('methods', {})
            for m_name, m_desc in methods.items():
                tool_name = f"{path_prefix}_{m_name}"

                # Check if tool already exists (FastMCP might raise or warn)
                # We can pre-check? FastMCP doesn't expose easy check.
                # But names should be unique by prefix.

                factory = resource_accessor_factory

                try:
                    wrapper = create_tool_wrapper(
                        factory,
                        path_prefix,
                        m_name,
                        tool_name,
                        m_desc
                    )
                    mcp.add_tool(wrapper)
                    _registered_tools.append({
                        "name": tool_name,
                        "description": m_desc.get('description', '')
                    })
                except Exception as e:
                    # Tool already exists or failed to register
                    # We log but continue to try registering other tools
                    logger.debug(f"Failed to register tool {tool_name}: {e}")

            resources = resource_desc.get('resources', {})
            for r_name, r_desc in resources.items():
                new_prefix = f"{path_prefix}_{r_name}"

                def make_sub_factory(parent_factory, res_name):
                    return lambda: getattr(parent_factory(), res_name)()

                sub_factory = make_sub_factory(resource_accessor_factory, r_name)

                process_resource(r_desc, new_prefix, sub_factory)

        prefix = api_name.title()

        def service_factory():
            # Pass captured scopes to get_service
            return get_service(api_name, api_version, scopes=scopes)

        process_resource(rd, prefix, service_factory)

        # logger.info(f"Registered tools for {api_name} {api_version}")

    except Exception as e:
        logger.warning(f"Skipping {api_name} {api_version} due to error: {e}")

def main():
    targets = get_target_apis()

    if not targets:
        logger.warning("No APIs found to register.")
        return

    logger.info(f"Registering tools for {len(targets)} APIs. This may take a while...")

    # Parallelize tool registration
    # We use ThreadPoolExecutor because build() is I/O bound (network)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(register_tools_for_api, name, version): (name, version) for name, version in targets}

        for future in concurrent.futures.as_completed(futures):
            name, version = futures[future]
            try:
                future.result()
            except Exception as exc:
                logger.error(f"{name} {version} generated an exception: {exc}")

    logger.info("Tool registration complete.")
    mcp.run()

# Register the list_tools utility
@mcp.tool()
def list_google_tools(prefix: str = None, include_descriptions: bool = False) -> str:
    """
    Lists available Google API tools registered on this server.

    Args:
        prefix: Optional prefix to filter tool names (case-insensitive).
        include_descriptions: If True, includes tool descriptions in the output.
    """
    results = []
    for tool in _registered_tools:
        name = tool['name']
        if prefix and not name.lower().startswith(prefix.lower()):
            continue

        if include_descriptions:
            desc = tool['description'].split('\n')[0] # First line only
            results.append(f"{name}: {desc}")
        else:
            results.append(name)

    return "\n".join(results)

@mcp.tool()
def list_google_scopes(api_filter: str = None) -> str:
    """
    Lists the OAuth2 scopes required for the registered Google APIs.
    Returns a `gcloud auth` command to authenticate with these scopes.

    Args:
        api_filter: Optional filter for API name (e.g. 'people' or 'translate').
    """
    all_scopes = set()

    matched_apis = []

    for api_key, scopes in _api_scopes.items():
        if api_filter and api_filter.lower() not in api_key.lower():
            continue

        matched_apis.append(api_key)
        for s in scopes:
            all_scopes.add(s)

    if not all_scopes:
        return "No scopes found matching filter."

    sorted_scopes = sorted(list(all_scopes))

    output = []
    output.append(f"Found {len(sorted_scopes)} scopes for {len(matched_apis)} APIs:")
    for s in sorted_scopes:
        output.append(f"- {s}")

    output.append("\nTo authenticate with these scopes, run:")
    output.append("\ngcloud auth application-default login --scopes " + ",".join(sorted_scopes))

    return "\n".join(output)

if __name__ == "__main__":
    main()
