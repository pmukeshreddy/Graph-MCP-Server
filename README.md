# Graph MCP Server

**An MCP server that gives LLMs actual code intelligence.**

## What This Does

When you ask an LLM to modify code, it usually guesses what else might break. This server builds a **real dependency graph** from your codebase so your AI assistant knows *exactly* what depends on what.

```
You: "Add timeout to APIRouter"

Without this server:          With this server:
LLM guesses → 🎲              LLM queries graph → gets real data
                              • 12 functions call APIRouter
                              • 3 files will need updates  
                              • Here's the actual code that breaks
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         MCP Server                              │
│                        (FastMCP)                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ GraphBuilder │    │ RuleMatcher  │    │ TestLocator  │      │
│  │              │    │              │    │              │      │
│  │ • tree-sitter│    │ • sentence-  │    │ • filesystem │      │
│  │   AST parse  │    │   transformers│   │   scanning   │      │
│  │ • NetworkX   │    │ • semantic   │    │ • regex      │      │
│  │   DiGraph    │    │   matching   │    │   analysis   │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## How It Actually Works

### 1. AST Parsing (tree-sitter)

**Not regex. Actual parsing.**

tree-sitter builds a full syntax tree, so we can accurately extract:
- Class definitions with their methods
- Function definitions (standalone + methods)
- Import statements and what they import
- **Call sites** - which functions call which

```python
# tree-sitter gives us this structure:
class_definition
  ├── name: "APIRouter"
  ├── body:
  │   ├── function_definition
  │   │   ├── name: "get_route_handler"  
  │   │   └── body: [call: "Depends", call: "Response", ...]
```

The parser walks every node, extracts symbols, and tracks what each function **actually calls** - not just imports.

**Fallback**: If tree-sitter isn't available, there's a regex-based parser. It works, but misses nested structures and call tracking.

### 2. Graph Building (NetworkX)

Every symbol becomes a node. Edges represent relationships:

| Edge Type | Meaning |
|-----------|---------|
| `contains` | Class → Method |
| `imports` | File → File |
| `calls` | Function → Function (confirmed call site) |
| `may_call` | Function → Function (name match, unconfirmed) |
| `instantiates` | Function → Class (creates instance) |
| `instantiates_uses` | Function → Class method (uses via instance) |
| `references` | Symbol name appears in another file |

**Blast Radius Query**: When you ask "what breaks if I change X":

```
predecessors(X) → things that DEPEND on X (will break)
successors(X)   → things X DEPENDS on (context you need)
```

One-level transitive closure catches indirect dependencies.

### 3. Symbol Indexing

Two main indexes:

```python
symbol_locations: Dict[str, Dict]
# "APIRouter" → {"file": "router.py", "line": 45, "code": "...", "type": "class"}
# "APIRouter.get_route_handler" → {"file": "router.py", "line": 52, ...}

file_symbols: Dict[str, List[str]]  
# "router.py" → ["APIRouter", "APIRouter.get_route_handler", "APIRouter.__init__"]
```

Symbols use qualified names (`Class.method`) so methods don't collide.

### 4. Rule Matching (sentence-transformers)

If you have coding rules (like `agents.md`), they get embedded using `all-MiniLM-L6-v2`.

When querying context, the affected symbols/files get embedded too, and we find rules with high cosine similarity.

**Fallback**: Keyword matching if sentence-transformers unavailable.

### 5. Test Discovery

Scans standard test directories (`tests/`, `test/`, etc.) for test files. Checks if test files:
- Import affected modules
- Reference affected symbol names

No coverage integration - just finds relevant test files.

## MCP Tools

| Tool | What It Returns |
|------|-----------------|
| `init_project` | Parses codebase, builds graph. **Call this first.** |
| `query_blast_radius` | Dependents, dependencies, affected files for a symbol |
| `get_full_context` | Everything: code, deps, rules, tests in one call |
| `find_symbols` | Search symbols by name pattern |
| `get_symbol_code` | Source code for a specific symbol |
| `get_file_symbols` | All symbols defined in a file |
| `get_related_tests` | Test files related to symbols/files |
| `match_coding_rules` | Rules relevant to context |
| `analyze_diff` | Parse git diff, find affected symbols |

## Setup

### 1. Install

```bash
cd graph-mcp-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure MCP Client

**Cursor** (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "code-graph": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/graph-mcp-server/mcp_server.py"]
    }
  }
}
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac):
```json
{
  "mcpServers": {
    "code-graph": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/graph-mcp-server/mcp_server.py"]
    }
  }
}
```

### 3. Use

```
> Initialize code graph for /Users/me/myproject

> What would break if I change the User class?

> Get full context for PaymentProcessor
```

## Files

```
graph-mcp-server/
├── mcp_server.py       # FastMCP server - exposes tools
├── graph_builder.py    # AST parsing + NetworkX graph
├── rule_matcher.py     # Semantic rule matching  
├── test_locator.py     # Test file discovery
├── github_client.py    # GitHub API (optional)
├── cli.py              # CLI for testing locally
└── requirements.txt
```



**What can be slow:**
- Initial `init_project` on large codebases (AST parsing is O(n))
- sentence-transformers model loading (~2s first call)

## Dependencies

- `fastmcp` - MCP server framework
- `tree-sitter` + `tree-sitter-python` - AST parsing
- `networkx` - Graph data structure
- `sentence-transformers` - Rule embedding (optional)
- `aiohttp` - GitHub API (optional)
