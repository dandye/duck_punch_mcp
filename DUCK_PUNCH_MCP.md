# Duck Punch an SDK until it Quacks like an MCP Server

In the rapidly evolving world of AI agents, the **Model Context Protocol (MCP)** has emerged as a standard for connecting LLMs to external tools and data. But here's a common dilemma: you have a perfectly good Python SDK for your internal API, but it's built for *humans*, not *LLMs*.

Human-centric SDKs typically look like this:
```python
client = MyClient(api_key="...")
client.create_widget(name="gizmo", size=10)
```

An LLM, however, just wants to call `create_widget(name="gizmo", size=10)`. It shouldn't be responsible for managing your authentication state, instantiating client objects, or knowing your API keys.

We could rewrite a new "Agent SDK," but who has time for that? Instead, we decided to **"duck punch"** our existing SDK to make it quack like an MCP server.

## What is Duck Punching?

"Duck punching" (a playful variation of "monkey patching") refers to modifying or extending code at runtime to alter its behavior without changing the original source. In our case, we aren't changing the SDK's logic, but we are dynamically wrapping it to adapt its interface for the MCP protocol.

## The Strategy

We built a lightweight adapter (`mcp_server.py`) that performs four key magic tricks:

### 1. Dynamic Discovery
Rather than manually registering every single function (and forgetting to update them later), our server iterates through the SDK's modules at startup. It uses Python's `pkgutil` and `inspect` to find any function that matches our SDK's pattern (e.g., functions that take a `client` as their first argument).

### 2. Dependency Injection
The LLM doesn't have the `client` object, but our server does. We create a wrapper function that automatically injects the authenticated client instance into the original function call.

```python
# Conceptually:
def wrapper(*args, **kwargs):
    authenticated_client = get_global_client()
    return original_function(authenticated_client, *args, **kwargs)
```

### 3. Signature Morphing
This is the most critical step. If we just exposed the wrapper, the MCP protocol would inspect the function and tell the LLM: *"This tool requires a `client` argument."* The LLM would then hallucinate a client object or fail.

We fix this by modifying the wrapper's `__signature__`. We strip the `client` parameter from the signature completely. Now, the LLM sees exactly what we want it to see:
*   **Original:** `create_widget(client, name, size)`
*   **MCP Tool:** `create_widget(name, size)`

### 4. Documentation Overrides
SDK docstrings are often technical ("Returns a `Widget` object..."). LLMs sometimes need more context ("Use this tool to create a new inventory item...").

We added a system to load external Markdown files and hot-swap the docstrings on our wrappers. This lets us tune the "prompt engineering" for our tools without polluting the clean technical code of the SDK.

## The Result

With a single script, we instantly exposed over 140 SDK functions as MCP tools. We didn't have to maintain a separate library, and as the core SDK improves, our MCP server automatically inherits the new capabilities.

By treating our legacy code as a dynamic resource, we bridged the gap between traditional software engineering and the new age of Agentic AI.
