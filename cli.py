#!/usr/bin/env python3
"""
Standalone CLI for testing Graph-MCP tools without MCP server.
Usage:
    # Graph tools (no API key needed)
    python cli.py query User --project ./my-project
    python cli.py find payment --project ./my-project
    
    # Claude AI tools (needs ANTHROPIC_API_KEY)
    python cli.py route "Refactor the User class"
    python cli.py generate "Add email validation" --symbol User --project ./my-project
    python cli.py pipeline "Refactor User class" --project ./my-project
    python cli.py explain User --project ./my-project
"""
import os
import sys
import json
import asyncio
import argparse
from pathlib import Path

from graph_builder import GraphBuilder
from rule_matcher import RuleMatcher
from test_locator import TestLocator
from github_client import GitHubClient

# Optional Claude import
try:
    from anthropic import Anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False


class CLI:
    def __init__(self):
        self.graph_builder = None
        self.rule_matcher = None
        self.test_locator = None
        self.github = GitHubClient(os.getenv("GITHUB_TOKEN"))
        self.project_root = None
        
        if CLAUDE_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            self.claude = Anthropic()
        else:
            self.claude = None
    
    # === Router Prompt ===
    ROUTER_PROMPT = """You are a code task router. Your job is to:
1. Detect if the user request requires codebase context
2. Extract the target symbol (class, function, variable name) to analyze

CRITICAL: The target_symbol must be an EXISTING symbol in the codebase.

Respond ONLY with valid JSON:
{
    "needs_context": true/false,
    "task_type": "refactor|add_feature|fix_bug|explain|other",
    "target_symbol": "ExistingSymbolName" or null,
    "confidence": 0.0-1.0
}"""

    # === Generator Prompt ===
    GENERATOR_PROMPT = """You are an expert software engineer. You receive:
1. TARGET CODE: The symbol to modify
2. BLAST RADIUS: Dependent code that may need updates
3. CALLED CODE: Code the target calls
4. ACTIVE RULES: Coding standards
5. TEST CONTEXT: Existing test patterns

Generate the complete solution in ONE response.

CRITICAL REQUIREMENTS:
- New parameters must be USED, not just stored
- If adding a parameter, show how it propagates through the call chain
- If the feature needs middleware/handlers, include them
- Make sure the feature actually WORKS end-to-end

FORMAT:
## CHANGES SUMMARY
Brief description.

## MODIFIED CODE
```python
# filename: path/to/file.py
<code>
```

## TESTS
```python
# filename: tests/test_feature.py
<test code>
```

## SUMMARY
Brief explanation."""

    VALIDATOR_PROMPT = """You are a senior code reviewer. Review the generated code and check:

1. COMPLETENESS: Is the feature fully implemented or just scaffolded?
2. PROPAGATION: If a new parameter was added, is it actually used/passed through?
3. ENFORCEMENT: If adding behavior (like timeout), is it actually enforced?
4. EDGE CASES: Are error cases handled?
5. TESTS: Do tests verify the feature WORKS, not just that it exists?

If issues found, provide SPECIFIC fixes with code.

Respond with:
## REVIEW STATUS
COMPLETE or INCOMPLETE

## ISSUES FOUND
List specific issues

## FIXES REQUIRED
```python
# filename: path/to/file.py
<fixed code>
```
"""

    IMPLEMENTATION_PATTERNS_PROMPT = """Analyze how similar features are implemented in this codebase.
Look at how existing parameters flow through the system.
Provide a pattern guide for implementing the new feature."""

    def init(self, project_root: str, rules_path: str = None):
        """Initialize graph for project."""
        self.project_root = project_root
        print(f"Building graph for: {project_root}")
        
        self.graph_builder = GraphBuilder(project_root)
        self.graph_builder.build()
        
        if rules_path and Path(rules_path).exists():
            self.rule_matcher = RuleMatcher(rules_path)
            print(f"Loaded rules from: {rules_path}")
        
        self.test_locator = TestLocator(project_root)
        
        print(f"✓ Symbols found: {len(self.graph_builder.symbol_locations)}")
        print(f"✓ Files parsed: {len(self.graph_builder.file_symbols)}")
        return self
    
    def query(self, symbol: str):
        """Query blast radius for symbol."""
        if not self.graph_builder:
            print("Error: Run 'init' first")
            return
        
        result = self.graph_builder.query_blast_radius(symbol)
        print(json.dumps(result, indent=2, default=str))
    
    def find(self, query: str, symbol_type: str = "all"):
        """Find symbols matching query."""
        if not self.graph_builder:
            print("Error: Run 'init' first")
            return
        
        matches = []
        for sym, info in self.graph_builder.symbol_locations.items():
            if query.lower() in sym.lower():
                if symbol_type == "all" or info.get("type") == symbol_type:
                    matches.append({
                        "symbol": sym,
                        "file": info.get("file"),
                        "line": info.get("line"),
                        "type": info.get("type")
                    })
        
        print(json.dumps({"matches": matches[:20], "total": len(matches)}, indent=2))
    
    def usages(self, symbol: str):
        """Find usages of symbol."""
        if not self.graph_builder:
            print("Error: Run 'init' first")
            return
        
        blast = self.graph_builder.query_blast_radius(symbol)
        if "error" in blast:
            print(json.dumps(blast, indent=2))
            return
        
        usages = []
        symbol_name = symbol.split(".")[-1]
        
        for dep in blast["dependents"]:
            dep_info = self.graph_builder.symbol_locations.get(dep, {})
            code = dep_info.get("code", "")
            
            for i, line in enumerate(code.split("\n")):
                if symbol_name in line:
                    usages.append({
                        "file": dep_info.get("file"),
                        "line": dep_info.get("line", 0) + i,
                        "snippet": line.strip()[:80],
                        "in_symbol": dep
                    })
        
        print(json.dumps({"usages": usages[:20], "count": len(usages)}, indent=2))
    
    def simulate(self, symbol: str, action: str, details: str = ""):
        """Simulate refactor impact."""
        if not self.graph_builder:
            print("Error: Run 'init' first")
            return
        
        blast = self.graph_builder.query_blast_radius(symbol)
        if "error" in blast:
            print(json.dumps(blast, indent=2))
            return
        
        breaking = []
        for dep in blast["dependents"]:
            dep_info = self.graph_builder.symbol_locations.get(dep, {})
            breaking.append({
                "file": dep_info.get("file"),
                "symbol": dep,
                "reason": f"Needs update for {action}: {details}"
            })
        
        result = {
            "symbol": symbol,
            "action": action,
            "files_to_update": blast["affected_files"],
            "breaking_changes": breaking[:20],
            "risk_score": min(len(breaking) / 10, 1.0)
        }
        print(json.dumps(result, indent=2))
    
    def rules(self, symbols: list = None, files: list = None):
        """Match rules to context."""
        if not self.rule_matcher:
            print("Error: No rules loaded. Run init with --rules")
            return
        
        result = self.rule_matcher.match_rules({
            "symbol": symbols[0] if symbols else "",
            "affected_files": files or [],
            "dependents": symbols or []
        })
        print(json.dumps(result, indent=2, default=str))
    
    def tests(self, symbol: str = None, files: list = None):
        """Find related tests."""
        if not self.test_locator:
            print("Error: Run 'init' first")
            return
        
        symbols = [symbol] if symbol else []
        result = self.test_locator.find_tests(files or [], symbols)
        print(json.dumps(result, indent=2))
    
    # === Claude AI Tools ===
    
    def _check_claude(self):
        if not self.claude:
            print("Error: ANTHROPIC_API_KEY not set or anthropic not installed")
            print("Run: export ANTHROPIC_API_KEY='sk-ant-...'")
            sys.exit(1)
    
    def route(self, user_request: str):
        """Use Claude Haiku to route request."""
        self._check_claude()
        
        print(f"Routing: {user_request}")
        response = self.claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=self.ROUTER_PROMPT,
            messages=[{"role": "user", "content": user_request}]
        )
        
        content = response.content[0].text
        print(f"\nRouter output:")
        print(content)
        print(f"\nTokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")
    
    def _build_context(self, symbol: str) -> str:
        """Build context string for generator."""
        blast = self.graph_builder.query_blast_radius(symbol)
        if "error" in blast:
            return f"Error: {blast['error']}"
        
        parts = []
        
        # Target
        parts.append("## TARGET SYMBOL")
        parts.append(f"Symbol: {blast['symbol']}")
        parts.append(f"File: {blast['symbol_info'].get('file')}")
        parts.append(f"```python\n{blast['symbol_info'].get('code', 'N/A')}\n```")
        
        # Blast Radius
        parts.append("\n## BLAST RADIUS")
        parts.append(f"Affected files: {blast['affected_files']}")
        parts.append(f"Dependents: {blast['dependents'][:10]}")
        
        # Dependent code
        for dep in blast["dependents"][:3]:
            code = self.graph_builder.get_symbol_code(dep)
            if code:
                parts.append(f"\n**{dep}**:\n```python\n{code[:500]}\n```")
        
        # Rules
        if self.rule_matcher:
            rules = self.rule_matcher.match_rules({
                "symbol": symbol,
                "affected_files": blast["affected_files"],
                "dependents": blast["dependents"]
            })
            if rules:
                parts.append("\n## RULES")
                for r in rules[:5]:
                    parts.append(f"- {r.get('text')}")
        
        return "\n".join(parts)
    
    def generate(self, user_request: str, symbol: str):
        """Use Claude Sonnet to generate code."""
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        print(f"Generating for symbol: {symbol}")
        context = self._build_context(symbol)
        
        prompt = f"""USER REQUEST: {user_request}

CONTEXT:
{context}

Generate the complete solution."""

        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            system=self.GENERATOR_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        
        print("\n" + "="*60)
        print("GENERATED SOLUTION")
        print("="*60)
        print(response.content[0].text)
        print(f"\nTokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")
    
    def pipeline(self, user_request: str):
        """Run full pipeline: Route -> Context -> Generate."""
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        # Phase 1: Route
        print("="*60)
        print("PHASE 1: ROUTER (Claude Haiku)")
        print("="*60)
        
        response = self.claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=self.ROUTER_PROMPT,
            messages=[{"role": "user", "content": user_request}]
        )
        
        try:
            content = response.content[0].text.strip()
            if "```" in content:
                content = content.split("```")[1].replace("json", "").strip()
            route = json.loads(content)
        except:
            print(f"Router parse error: {response.content[0].text}")
            return
        
        print(f"Needs context: {route.get('needs_context')}")
        print(f"Target symbol: {route.get('target_symbol')}")
        print(f"Task type: {route.get('task_type')}")
        
        if not route.get("needs_context"):
            print("\nNo code context needed.")
            return
        
        symbol = route.get("target_symbol")
        if not symbol:
            print("\nCould not extract symbol.")
            return
        
        # Phase 2: Context
        print("\n" + "="*60)
        print("PHASE 2: CONTEXT (Graph Engine)")
        print("="*60)
        
        blast = self.graph_builder.query_blast_radius(symbol)
        if "error" in blast:
            print(f"Error: {blast['error']}")
            print(f"Suggestions: {blast.get('suggestions', [])}")
            return
        
        print(f"Symbol: {blast['symbol']}")
        print(f"Blast radius: {blast['blast_radius_size']} symbols")
        print(f"Affected files: {blast['affected_files']}")
        
        # Phase 3: Generate
        print("\n" + "="*60)
        print("PHASE 3: GENERATOR (Claude Sonnet)")
        print("="*60)
        
        context = self._build_context(symbol)
        prompt = f"""USER REQUEST: {user_request}

CONTEXT:
{context}

Generate the complete solution."""

        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            system=self.GENERATOR_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        
        print("\n" + response.content[0].text)
        print(f"\nTokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")
    
    def explain(self, symbol: str):
        """Use Claude to explain code."""
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        context = self._build_context(symbol)
        
        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            messages=[{"role": "user", "content": f"Explain this code:\n\n{context}"}]
        )
        
        print(response.content[0].text)
    
    def _find_similar_patterns(self, symbol: str, feature_type: str) -> str:
        """Find how similar features are implemented in the codebase."""
        # Look for similar parameter patterns in the target class
        blast = self.graph_builder.query_blast_radius(symbol)
        if "error" in blast:
            return ""
        
        target_code = blast["symbol_info"].get("code", "")
        
        # Find other parameters in the class and how they're used
        patterns = []
        
        # Get code from dependencies to see how params flow
        for dep in blast.get("dependencies", [])[:5]:
            dep_code = self.graph_builder.get_symbol_code(dep)
            if dep_code:
                patterns.append(f"**{dep}**:\n```python\n{dep_code[:800]}\n```")
        
        return "\n".join(patterns)
    
    def _validate_implementation(self, generated_code: str, user_request: str) -> dict:
        """Validate the generated code for completeness."""
        self._check_claude()
        
        prompt = f"""Review this generated code for the request: "{user_request}"

GENERATED CODE:
{generated_code}

Check if the implementation is complete and functional."""

        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4000,
            system=self.VALIDATOR_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        
        result = response.content[0].text
        is_complete = "COMPLETE" in result.split("\n")[0:5]
        
        return {
            "review": result,
            "is_complete": "## REVIEW STATUS\nCOMPLETE" in result or "STATUS\nCOMPLETE" in result,
            "tokens": response.usage.input_tokens + response.usage.output_tokens
        }
    
    def pipeline_v2(self, user_request: str):
        """
        Enhanced pipeline with validation:
        1. Route (Haiku)
        2. Find implementation patterns
        3. Generate (Sonnet)
        4. Validate & Fix (Sonnet)
        """
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        # Phase 1: Route
        print("="*60)
        print("PHASE 1: ROUTER (Claude Haiku)")
        print("="*60)
        
        response = self.claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=self.ROUTER_PROMPT,
            messages=[{"role": "user", "content": user_request}]
        )
        
        try:
            content = response.content[0].text.strip()
            if "```" in content:
                content = content.split("```")[1].replace("json", "").strip()
            route = json.loads(content)
        except:
            print(f"Router parse error: {response.content[0].text}")
            return
        
        print(f"Target symbol: {route.get('target_symbol')}")
        print(f"Task type: {route.get('task_type')}")
        
        symbol = route.get("target_symbol")
        if not symbol:
            print("\nCould not extract symbol.")
            return
        
        # Phase 2: Find Implementation Patterns
        print("\n" + "="*60)
        print("PHASE 2: PATTERN ANALYSIS")
        print("="*60)
        
        blast = self.graph_builder.query_blast_radius(symbol)
        if "error" in blast:
            print(f"Error: {blast['error']}")
            return
        
        # Find how similar params are implemented
        patterns = self._find_similar_patterns(symbol, route.get("task_type", ""))
        print(f"Found {len(patterns.split('**')) - 1} implementation patterns")
        
        # Phase 3: Generate with Enhanced Context
        print("\n" + "="*60)
        print("PHASE 3: GENERATOR (Claude Sonnet)")
        print("="*60)
        
        context = self._build_context(symbol)
        
        # Add implementation patterns to prompt
        enhanced_prompt = f"""USER REQUEST: {user_request}

CONTEXT:
{context}

IMPLEMENTATION PATTERNS (how similar features work in this codebase):
{patterns}

CRITICAL: 
- Don't just ADD the parameter, make sure it's USED and ENFORCED
- Show the complete flow from parameter to actual behavior
- Look at how existing parameters (like dependencies, tags) propagate

Generate the complete, WORKING solution."""

        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            system=self.GENERATOR_PROMPT,
            messages=[{"role": "user", "content": enhanced_prompt}]
        )
        
        generated_code = response.content[0].text
        gen_tokens = response.usage.input_tokens + response.usage.output_tokens
        
        # Phase 4: Validate
        print("\n" + "="*60)
        print("PHASE 4: VALIDATION (Claude Sonnet)")
        print("="*60)
        
        validation = self._validate_implementation(generated_code, user_request)
        
        if validation["is_complete"]:
            print("✅ Implementation is COMPLETE")
        else:
            print("⚠️  Implementation has ISSUES - generating fixes...")
        
        # Phase 5: Output
        print("\n" + "="*60)
        print("FINAL OUTPUT")
        print("="*60)
        
        print("\n### GENERATED CODE ###")
        print(generated_code)
        
        print("\n### VALIDATION REVIEW ###")
        print(validation["review"])
        
        total_tokens = gen_tokens + validation["tokens"]
        print(f"\nTotal tokens: {total_tokens}")
    
    def pipeline_complete(self, user_request: str, max_iterations: int = 3):
        """
        Full pipeline with iterative refinement until complete.
        Keeps refining until validator approves or max iterations reached.
        """
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        # Phase 1: Route
        print("="*60)
        print("PHASE 1: ROUTER")
        print("="*60)
        
        response = self.claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=self.ROUTER_PROMPT,
            messages=[{"role": "user", "content": user_request}]
        )
        
        try:
            content = response.content[0].text.strip()
            if "```" in content:
                content = content.split("```")[1].replace("json", "").strip()
            route = json.loads(content)
        except:
            print(f"Router parse error")
            return
        
        symbol = route.get("target_symbol")
        if not symbol:
            print("Could not extract symbol.")
            return
        
        print(f"Target: {symbol}")
        
        # Get context
        context = self._build_context(symbol)
        patterns = self._find_similar_patterns(symbol, route.get("task_type", ""))
        
        generated_code = None
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            print(f"\n{'='*60}")
            print(f"ITERATION {iteration}/{max_iterations}")
            print("="*60)
            
            if generated_code is None:
                # First generation
                prompt = f"""USER REQUEST: {user_request}

CONTEXT:
{context}

IMPLEMENTATION PATTERNS:
{patterns}

Generate a COMPLETE, WORKING implementation. The feature must actually work, not just be scaffolded."""
            else:
                # Refinement based on validation feedback
                prompt = f"""USER REQUEST: {user_request}

PREVIOUS ATTEMPT:
{generated_code}

VALIDATION FEEDBACK:
{validation["review"]}

Fix ALL the issues identified. Make sure the feature actually WORKS end-to-end."""

            response = self.claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=8192,
                system=self.GENERATOR_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            
            generated_code = response.content[0].text
            
            # Validate
            print("Validating...")
            validation = self._validate_implementation(generated_code, user_request)
            
            if validation["is_complete"]:
                print(f"✅ COMPLETE after {iteration} iteration(s)")
                break
            else:
                print(f"⚠️  Issues found, refining...")
        
        # Final output
        print("\n" + "="*60)
        print("FINAL IMPLEMENTATION")
        print("="*60)
        print(generated_code)
        
        if not validation["is_complete"]:
            print("\n⚠️  Note: Max iterations reached. Review manually.")
            print("\n### REMAINING ISSUES ###")
            print(validation["review"])
    
    def apply(self, user_request: str, auto_apply: bool = False):
        """Generate code AND apply changes to files."""
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        # Phase 1: Route
        print("="*60)
        print("PHASE 1: ROUTING")
        print("="*60)
        
        response = self.claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=self.ROUTER_PROMPT,
            messages=[{"role": "user", "content": user_request}]
        )
        
        try:
            content = response.content[0].text.strip()
            if "```" in content:
                content = content.split("```")[1].replace("json", "").strip()
            route = json.loads(content)
        except:
            print(f"Router error: {response.content[0].text}")
            return
        
        symbol = route.get("target_symbol")
        if not symbol:
            print("Could not extract symbol")
            return
        
        print(f"Target: {symbol}")
        
        # Phase 2: Generate with structured output
        print("\n" + "="*60)
        print("PHASE 2: GENERATING CODE")
        print("="*60)
        
        context = self._build_context(symbol)
        
        gen_prompt = f"""USER REQUEST: {user_request}

CONTEXT:
{context}

Generate the solution. For EACH file change, use this EXACT format:

<<<FILE: path/to/file.py>>>
```python
# Complete new content for this file or function
```
<<<END_FILE>>>

Include ALL files that need changes."""

        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            system=self.GENERATOR_PROMPT,
            messages=[{"role": "user", "content": gen_prompt}]
        )
        
        output = response.content[0].text
        print(output)
        
        # Phase 3: Parse and apply
        print("\n" + "="*60)
        print("PHASE 3: APPLYING CHANGES")
        print("="*60)
        
        changes = self._parse_file_changes(output)
        
        if not changes:
            print("No file changes detected in output.")
            print("Looking for code blocks...")
            changes = self._parse_code_blocks(output)
        
        if not changes:
            print("Could not parse any file changes.")
            return
        
        print(f"\nFound {len(changes)} file(s) to modify:")
        for filepath, _ in changes:
            full_path = Path(self.project_root) / filepath
            exists = "EXISTS" if full_path.exists() else "NEW"
            print(f"  - {filepath} [{exists}]")
        
        if not auto_apply:
            confirm = input("\nApply changes? [y/N]: ").strip().lower()
            if confirm != 'y':
                print("Aborted.")
                return
        
        # Apply changes
        for filepath, new_code in changes:
            full_path = Path(self.project_root) / filepath
            
            # Create directories if needed
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Backup existing file
            if full_path.exists():
                backup_path = full_path.with_suffix(full_path.suffix + '.bak')
                import shutil
                shutil.copy(full_path, backup_path)
                print(f"✓ Backed up: {filepath} -> {filepath}.bak")
            
            # Write new content
            full_path.write_text(new_code)
            print(f"✓ Updated: {filepath}")
        
        print(f"\n✓ Applied {len(changes)} changes!")
    
    def _parse_file_changes(self, output: str) -> list:
        """Parse <<<FILE: path>>> blocks from output."""
        import re
        
        changes = []
        pattern = r'<<<FILE:\s*([^\n>]+)>>>\s*```\w*\n(.*?)```\s*<<<END_FILE>>>'
        
        for match in re.finditer(pattern, output, re.DOTALL):
            filepath = match.group(1).strip()
            code = match.group(2).strip()
            changes.append((filepath, code))
        
        return changes
    
    def _parse_code_blocks(self, output: str) -> list:
        """Fallback: parse # filename: comments in code blocks."""
        import re
        
        changes = []
        pattern = r'```python\n#\s*filename:\s*([^\n]+)\n(.*?)```'
        
        for match in re.finditer(pattern, output, re.DOTALL):
            filepath = match.group(1).strip()
            code = match.group(2).strip()
            changes.append((filepath, code))
        
        return changes
    
    def patch(self, user_request: str, symbol: str):
        """Generate a patch/diff instead of full file replacement."""
        self._check_claude()
        
        if not self.graph_builder:
            print("Error: Run init first")
            return
        
        context = self._build_context(symbol)
        
        patch_prompt = f"""USER REQUEST: {user_request}

CONTEXT:
{context}

Generate a UNIFIED DIFF (patch) for each file that needs changes.
Format:
```diff
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -line,count +line,count @@
 context line
-removed line
+added line
 context line
```

Only include the specific functions/methods being changed, not entire files."""

        response = self.claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            messages=[{"role": "user", "content": patch_prompt}]
        )
        
        patch_output = response.content[0].text
        print(patch_output)
        
        # Save patch file
        patch_file = Path(self.project_root) / "changes.patch"
        patch_file.write_text(patch_output)
        print(f"\n✓ Patch saved to: {patch_file}")
        print(f"Apply with: cd {self.project_root} && git apply changes.patch")
    
    # === GitHub Tools ===
    
    async def pr_blast(self, owner: str, repo: str, pr_number: int, local_path: str):
        """Analyze PR blast radius."""
        print(f"Fetching PR #{pr_number} from {owner}/{repo}...")
        
        diff_result = await self.github.get_pull_request_diff(owner, repo, pr_number)
        if "error" in diff_result:
            print(json.dumps(diff_result, indent=2))
            return
        
        self.init(local_path)
        
        import re
        diff_text = diff_result.get("diff", "")
        symbols = []
        
        for line in diff_text.split("\n"):
            match = re.match(r'^\+\s*(?:class|def|async def)\s+(\w+)', line)
            if match:
                symbols.append(match.group(1))
        
        all_affected = set()
        all_dependents = []
        
        for sym in symbols:
            blast = self.graph_builder.query_blast_radius(sym)
            if "error" not in blast:
                all_affected.update(blast["affected_files"])
                all_dependents.extend(blast["dependents"])
        
        result = {
            "pr_number": pr_number,
            "changed_symbols": symbols,
            "affected_files": list(all_affected),
            "affected_symbols": list(set(all_dependents))[:20],
            "risk_score": min(len(all_affected) / 10, 1.0)
        }
        print(json.dumps(result, indent=2))
    
    async def github_file(self, owner: str, repo: str, path: str):
        """Get file from GitHub."""
        result = await self.github.get_file_contents(owner, repo, path)
        print(json.dumps(result, indent=2))
    
    async def github_commits(self, owner: str, repo: str, path: str = None):
        """List commits."""
        result = await self.github.list_commits(owner, repo, path)
        print(json.dumps(result, indent=2))
    
    async def github_pr(self, owner: str, repo: str, pr_number: int):
        """Get PR details."""
        result = await self.github.get_pull_request(owner, repo, pr_number)
        print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Graph-MCP CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # init
    p = subparsers.add_parser("init", help="Initialize project graph")
    p.add_argument("project_root")
    p.add_argument("--rules", "-r", help="Path to rules file")
    
    # query
    p = subparsers.add_parser("query", help="Query blast radius")
    p.add_argument("symbol")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r")
    
    # find
    p = subparsers.add_parser("find", help="Find symbols")
    p.add_argument("query")
    p.add_argument("--type", "-t", default="all", choices=["all", "class", "function", "method"])
    p.add_argument("--project", "-p", required=True)
    
    # usages
    p = subparsers.add_parser("usages", help="Find symbol usages")
    p.add_argument("symbol")
    p.add_argument("--project", "-p", required=True)
    
    # simulate
    p = subparsers.add_parser("simulate", help="Simulate refactor")
    p.add_argument("symbol")
    p.add_argument("action", choices=["add_parameter", "rename", "delete", "change_signature"])
    p.add_argument("details", nargs="?", default="")
    p.add_argument("--project", "-p", required=True)
    
    # rules
    p = subparsers.add_parser("rules", help="Match rules")
    p.add_argument("--symbols", "-s", nargs="+")
    p.add_argument("--files", "-f", nargs="+")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r", required=True)
    
    # tests
    p = subparsers.add_parser("tests", help="Find related tests")
    p.add_argument("--symbol", "-s")
    p.add_argument("--files", "-f", nargs="+")
    p.add_argument("--project", "-p", required=True)
    
    # === Claude AI Commands ===
    
    # route
    p = subparsers.add_parser("route", help="Use Claude Haiku to route request")
    p.add_argument("request", help="User request like 'Refactor User class'")
    
    # generate
    p = subparsers.add_parser("generate", help="Use Claude Sonnet to generate code")
    p.add_argument("request", help="What to generate")
    p.add_argument("--symbol", "-s", required=True, help="Target symbol")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r")
    
    # pipeline (full)
    p = subparsers.add_parser("pipeline", help="Run full pipeline: Route -> Context -> Generate")
    p.add_argument("request", help="Code request")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r")
    
    # pipeline-v2 (with validation)
    p = subparsers.add_parser("pipeline-v2", help="Enhanced pipeline with validation pass")
    p.add_argument("request", help="Code request")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r")
    
    # pipeline-complete (iterative refinement)
    p = subparsers.add_parser("pipeline-complete", help="Full pipeline with iterative refinement until complete")
    p.add_argument("request", help="Code request")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r")
    p.add_argument("--max-iter", "-m", type=int, default=3, help="Max refinement iterations")
    
    # explain
    p = subparsers.add_parser("explain", help="Use Claude to explain code")
    p.add_argument("symbol")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--rules", "-r")
    
    # === GitHub Commands ===
    
    # pr-blast
    p = subparsers.add_parser("pr-blast", help="Analyze PR blast radius")
    p.add_argument("owner")
    p.add_argument("repo")
    p.add_argument("pr_number", type=int)
    p.add_argument("local_path")
    
    # github-file
    p = subparsers.add_parser("github-file", help="Get file from GitHub")
    p.add_argument("owner")
    p.add_argument("repo")
    p.add_argument("path")
    
    # github-commits
    p = subparsers.add_parser("github-commits", help="List commits")
    p.add_argument("owner")
    p.add_argument("repo")
    p.add_argument("--path", "-f")
    
    # github-pr
    p = subparsers.add_parser("github-pr", help="Get PR details")
    p.add_argument("owner")
    p.add_argument("repo")
    p.add_argument("pr_number", type=int)
    
    args = parser.parse_args()
    cli = CLI()
    
    # Graph tools
    if args.command == "init":
        cli.init(args.project_root, args.rules)
    
    elif args.command == "query":
        cli.init(args.project, args.rules)
        cli.query(args.symbol)
    
    elif args.command == "find":
        cli.init(args.project)
        cli.find(args.query, args.type)
    
    elif args.command == "usages":
        cli.init(args.project)
        cli.usages(args.symbol)
    
    elif args.command == "simulate":
        cli.init(args.project)
        cli.simulate(args.symbol, args.action, args.details)
    
    elif args.command == "rules":
        cli.init(args.project, args.rules)
        cli.rules(args.symbols, args.files)
    
    elif args.command == "tests":
        cli.init(args.project)
        cli.tests(args.symbol, args.files)
    
    # Claude AI tools
    elif args.command == "route":
        cli.route(args.request)
    
    elif args.command == "generate":
        cli.init(args.project, getattr(args, 'rules', None))
        cli.generate(args.request, args.symbol)
    
    elif args.command == "pipeline":
        cli.init(args.project, getattr(args, 'rules', None))
        cli.pipeline(args.request)
    
    elif args.command == "pipeline-v2":
        cli.init(args.project, getattr(args, 'rules', None))
        cli.pipeline_v2(args.request)
    
    elif args.command == "pipeline-complete":
        cli.init(args.project, getattr(args, 'rules', None))
        cli.pipeline_complete(args.request, args.max_iter)
    
    elif args.command == "explain":
        cli.init(args.project, getattr(args, 'rules', None))
        cli.explain(args.symbol)
    
    # GitHub tools
    elif args.command == "pr-blast":
        asyncio.run(cli.pr_blast(args.owner, args.repo, args.pr_number, args.local_path))
    
    elif args.command == "github-file":
        asyncio.run(cli.github_file(args.owner, args.repo, args.path))
    
    elif args.command == "github-commits":
        asyncio.run(cli.github_commits(args.owner, args.repo, args.path))
    
    elif args.command == "github-pr":
        asyncio.run(cli.github_pr(args.owner, args.repo, args.pr_number))


if __name__ == "__main__":
    main()
