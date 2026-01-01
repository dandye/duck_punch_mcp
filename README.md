![Duck Punch MCP](logo.png)
# Duck Punch MCP

**Duck Punch MCP** is a collection of [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers that "duck punch" (dynamically wrap) existing Python SDKs to expose their functionality to LLMs.

Currently supports:
- **Google SecOps SDK** (Chronicle)
- **SOAR SDK** (Siemplify)

## Features

- **Dynamic Tool Discovery**: Automatically inspects SDK logic to register MCP tools.
- **Unified Interface**: Exposes complex SDK methods as standard MCP tools.
- **Documentation Overrides**: Injects LLM-friendly documentation where SDK docs are lacking.
- **Legacy Support**: Handles legacy imports (e.g., `Siemplify.py`) via dynamic path manipulation.

## Prerequisites

- **Python 3.10+** (Recommend 3.11 or 3.12)
- **uv** (Recommended for dependency management) or `pip`
- **Git** (for submodule management)

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-repo/duck_punch_mcp.git
    cd duck_punch_mcp
    ```

2.  **Initialize Submodules:**
    This project relies on external SDKs as submodules.
    ```bash
    git submodule update --init --recursive
    ```

3.  **Install Dependencies:**
    Using `uv` (Fast & Recommended):
    ```bash
    uv sync
    ```
    Or using standard `pip`:
    ```bash
    pip install -e .
    ```

## Configuration

1.  **Environment Variables**:
    Copy the example configuration and fill in your details.
    ```bash
    cp .env.example .env
    ```

2.  **Edit `.env`**:
    ```ini
    # Google SecOps
    PROJECT_ID=your-gcp-project-id
    CUSTOMER_ID=your-chronicle-customer-id
    CHRONICLE_REGION=us

    # SOAR SDK
    SIEMPLIFY_API_KEY=your-api-key
    ```

## Usage

### Running the SecOps Server

```bash
uv run python -m duck_punch_mcp.secops_server
```

### Running the SOAR Server

```bash
uv run python -m duck_punch_mcp.soar_server
```

### Inspecting Tools (Development)

You can use the MCP Inspector to test tools interactively:

```bash
npx @modelcontextprotocol/inspector uv run python -m duck_punch_mcp.secops_server
```

## Gemini CLI Configuration

To use these servers with the Gemini CLI, add the following to your MCP configuration file:

```json
{
  "mcpServers": {
    "secops": {
      "command": "uv",
      "args": ["run", "python", "-m", "duck_punch_mcp.secops_server"],
      "env": {
        "PROJECT_ID": "<YOUR_PROJECT_ID>",
        "CUSTOMER_ID": "<YOUR_CUSTOMER_ID>",
        "CHRONICLE_REGION": "us"
      }
    },
    "soar": {
      "command": "uv",
      "args": ["run", "python", "-m", "duck_punch_mcp.soar_server"],
      "env": {
        "SIEMPLIFY_API_KEY": "<YOUR_API_KEY>"
      }
    }
  }
}
```

## Architecture

The project uses a "duck punching" strategy to wrap external SDKs without modifying their source code:

- **src/duck_punch_mcp/secops_server.py**: Wraps `external/secops-wrapper`.
- **src/duck_punch_mcp/soar_server.py**: Wraps `external/soar-sdk`, injecting `SiemplifyUtils` and other legacy dependencies into `sys.path`.
- **src/duck_punch_mcp/mcp_docs/**: Contains documentation overrides (e.g., `get_alerts.md`) to improve LLM reasoning.

## Contributing

1.  Fork the repo.
2.  Create a feature branch.
3.  Submit a Pull Request.

## License

[License Name] - See LICENSE for details.
