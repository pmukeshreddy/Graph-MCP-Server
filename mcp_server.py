#!/usr/bin/env python3
"""
Graph-Backed MCP Server using FastMCP

MCP Server that Claude can USE to get code context.
"""
import os
import json
import re
from pathlib import Path
from fastmcp import FastMCP

from graph_builder import GraphBuilder
from rule_matcher import RuleMatcher
from test_locator import TestLocator

# Create FastMCP server
mcp = FastMCP("code-graph")

# Global state
_graph: GraphBuilder = None
_rules: RuleMatcher = None
_tests: TestLocator = None


@mcp.tool()
def init_project(project_path: str, rules_path: str = None) -> dict:
    """
    Initialize the code graph for a project. Call this first.
    
    Args:
        project_path: Absolute path to project root
        rules_path: Optional path to coding rules markdown file
    """
    global _graph, _rules, _tests
    
    _graph = GraphBuilder(project_path)
    _graph.build()
    
    if rules_path and Path(rules_path).exists():
        _rules = RuleMatcher(rules_path)
    else:
        _rules = None
    
    _tests = TestLocator(project_path)
    
    return {
        "status": "initialized",
        "project": project_path,
        "symbols_found": len(_graph.symbol_locations),
        "files_parsed": len(_graph.file_symbols)
    }


@mcp.tool()
def query_blast_radius(symbol: str) -> dict:
    """
    Get the blast radius for a symbol - all code that depends on it and all code it depends on.
    
    Args:
        symbol: Symbol name (e.g., 'User', 'PaymentProcessor', 'APIRouter')
    """
    if not _graph:
        return {"error": "Call init_project first"}
    
    result = _graph.query_blast_radius(symbol)
    
    if "error" in result:
        return result
    
    return {
        "symbol": result["symbol"],
        "file": result["symbol_info"].get("file"),
        "line": result["symbol_info"].get("line"),
        "type": result["symbol_info"].get("type"),
        "blast_radius_size": result["blast_radius_size"],
        "affected_files": result["affected_files"],
        "dependents": result["dependents"][:20],
        "dependencies": result["dependencies"][:20]
    }


@mcp.tool()
def find_symbols(query: str, symbol_type: str = "all") -> dict:
    """
    Search for symbols in the codebase.
    
    Args:
        query: Search pattern (case-insensitive)
        symbol_type: Filter by type - 'all', 'class', 'function', 'method'
    """
    if not _graph:
        return {"error": "Call init_project first"}
    
    matches = []
    query_lower = query.lower()
    
    for sym, info in _graph.symbol_locations.items():
        if query_lower in sym.lower():
            if symbol_type == "all" or info.get("type") == symbol_type:
                matches.append({
                    "symbol": sym,
                    "file": info.get("file"),
                    "line": info.get("line"),
                    "type": info.get("type")
                })
    
    return {
        "query": query,
        "matches": matches[:30],
        "total": len(matches)
    }


@mcp.tool()
def get_symbol_code(symbol: str) -> dict:
    """
    Get the source code for a symbol.
    
    Args:
        symbol: Exact symbol name
    """
    if not _graph:
        return {"error": "Call init_project first"}
    
    # Try exact match first
    if symbol not in _graph.symbol_locations:
        # Try partial match
        matches = [s for s in _graph.symbol_locations if symbol.lower() in s.lower()]
        if matches:
            symbol = matches[0]
        else:
            return {"error": f"Symbol '{symbol}' not found"}
    
    info = _graph.symbol_locations[symbol]
    return {
        "symbol": symbol,
        "file": info.get("file"),
        "line": info.get("line"),
        "type": info.get("type"),
        "code": info.get("code", "")
    }


@mcp.tool()
def get_file_symbols(file_path: str) -> dict:
    """
    List all symbols defined in a file.
    
    Args:
        file_path: Relative path to file
    """
    if not _graph:
        return {"error": "Call init_project first"}
    
    symbols = _graph.file_symbols.get(file_path, [])
    
    # Try partial match
    if not symbols:
        for fp, syms in _graph.file_symbols.items():
            if file_path in fp:
                symbols = syms
                file_path = fp
                break
    
    return {
        "file": file_path,
        "symbols": [
            {
                "name": s,
                "type": _graph.symbol_locations.get(s, {}).get("type"),
                "line": _graph.symbol_locations.get(s, {}).get("line")
            }
            for s in symbols
        ]
    }


@mcp.tool()
def get_related_tests(symbols: list[str] = None, files: list[str] = None) -> dict:
    """
    Find tests related to given symbols or files.
    
    Args:
        symbols: List of symbol names
        files: List of file paths
    """
    if not _tests:
        return {"error": "Call init_project first"}
    
    return _tests.find_tests(files or [], symbols or [])


@mcp.tool()
def match_coding_rules(symbols: list[str] = None, files: list[str] = None) -> dict:
    """
    Find coding rules that apply to given symbols or files.
    
    Args:
        symbols: Symbols to match rules for
        files: Files to match rules for
    """
    if not _rules:
        return {"rules": [], "message": "No rules file loaded"}
    
    context = {
        "symbol": symbols[0] if symbols else "",
        "affected_files": files or [],
        "dependents": symbols or []
    }
    
    rules = _rules.match_rules(context, top_k=10)
    return {
        "matched_rules": [
            {"id": r.get("id"), "text": r.get("text"), "score": r.get("relevance_score", 0)}
            for r in rules
        ]
    }


@mcp.tool()
def get_full_context(symbol: str, include_code: bool = True) -> dict:
    """
    Get complete context for modifying a symbol: code, dependencies, dependents, rules, tests.
    This is the main tool for understanding what you need to change.
    
    Args:
        symbol: Symbol to get context for
        include_code: Include source code snippets
    """
    if not _graph:
        return {"error": "Call init_project first"}
    
    # Call graph directly instead of other tools
    blast_result = _graph.query_blast_radius(symbol)
    if "error" in blast_result:
        return blast_result
    
    blast = {
        "symbol": blast_result["symbol"],
        "file": blast_result["symbol_info"].get("file"),
        "line": blast_result["symbol_info"].get("line"),
        "type": blast_result["symbol_info"].get("type"),
        "blast_radius_size": blast_result["blast_radius_size"],
        "affected_files": blast_result["affected_files"],
        "dependents": blast_result["dependents"][:20],
        "dependencies": blast_result["dependencies"][:20]
    }
    
    result = {
        "target": blast,
        "dependents_code": {},
        "dependencies_code": {},
        "rules": [],
        "tests": {}
    }
    
    if include_code:
        # Get target code directly from graph
        if symbol in _graph.symbol_locations:
            info = _graph.symbol_locations[symbol]
            result["target"]["code"] = info.get("code", "")[:2000]
        else:
            # Try partial match
            matches = [s for s in _graph.symbol_locations if symbol.lower() in s.lower()]
            if matches:
                info = _graph.symbol_locations[matches[0]]
                result["target"]["code"] = info.get("code", "")[:2000]
        
        # Get dependents code (things that use target)
        for dep in blast.get("dependents", [])[:5]:
            code = _graph.get_symbol_code(dep)
            if code:
                result["dependents_code"][dep] = code[:1000]
        
        # Get dependencies code (things target uses)
        for dep in blast.get("dependencies", [])[:5]:
            code = _graph.get_symbol_code(dep)
            if code:
                result["dependencies_code"][dep] = code[:1000]
    
    # Get rules
    if _rules:
        context = {
            "symbol": symbol,
            "affected_files": blast.get("affected_files", []),
            "dependents": [symbol]
        }
        rules = _rules.match_rules(context, top_k=10)
        result["rules"] = [
            {"id": r.get("id"), "text": r.get("text"), "score": r.get("relevance_score", 0)}
            for r in rules
        ]
    
    # Get tests
    if _tests:
        result["tests"] = _tests.find_tests(blast.get("affected_files", []), [symbol])
    
    return result


@mcp.tool()
def analyze_diff(diff: str) -> dict:
    """
    Analyze a git diff to find affected symbols and their blast radius.
    
    Args:
        diff: Git diff content
    """
    if not _graph:
        return {"error": "Call init_project first"}
    
    changed_symbols = []
    
    # Extract symbols from diff
    for line in diff.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            class_match = re.match(r'^\+\s*class\s+(\w+)', line)
            if class_match:
                changed_symbols.append(class_match.group(1))
            
            func_match = re.match(r'^\+\s*(?:async\s+)?def\s+(\w+)', line)
            if func_match:
                changed_symbols.append(func_match.group(1))
    
    # Context lines
    context_matches = re.findall(r'^@@.*@@\s*(?:class|def|async def)\s+(\w+)', diff, re.MULTILINE)
    changed_symbols.extend(context_matches)
    changed_symbols = list(set(changed_symbols))
    
    # Get blast radius for each
    all_affected = set()
    all_dependents = set()
    impacts = []
    
    for sym in changed_symbols:
        blast_result = _graph.query_blast_radius(sym)
        if "error" not in blast_result:
            all_affected.update(blast_result.get("affected_files", []))
            all_dependents.update(blast_result.get("dependents", []))
            impacts.append({"symbol": sym, "blast_radius": blast_result.get("blast_radius_size", 0)})
    
    return {
        "changed_symbols": changed_symbols,
        "affected_files": list(all_affected),
        "total_dependents": len(all_dependents),
        "impacts": impacts
    }


if __name__ == "__main__":
    mcp.run()