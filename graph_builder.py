"""Build dependency graph using tree-sitter AST parsing."""
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
import networkx as nx

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

class GraphBuilder:
    """Build and query code dependency graph."""
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.graph = nx.DiGraph()
        self.symbol_locations: Dict[str, Dict] = {}  # symbol -> {file, line, code}
        self.file_symbols: Dict[str, List[str]] = {}  # file -> [symbols]
        self.imports: Dict[str, Set[str]] = {}  # file -> {imported_files}
        
        if TREE_SITTER_AVAILABLE:
            self.parser = Parser(Language(tspython.language()))
        else:
            self.parser = None
    
    def build(self) -> nx.DiGraph:
        """Build the complete dependency graph."""
        py_files = list(self.project_root.rglob("*.py"))
        
        for py_file in py_files:
            if "__pycache__" in str(py_file):
                continue
            self._parse_file(py_file)
        
        self._resolve_dependencies()
        return self.graph
    
    def _parse_file(self, filepath: Path):
        """Parse a Python file and extract symbols."""
        try:
            code = filepath.read_text()
        except Exception:
            return
        
        rel_path = str(filepath.relative_to(self.project_root))
        self.file_symbols[rel_path] = []
        self.imports[rel_path] = set()
        
        if self.parser:
            self._parse_with_tree_sitter(code, rel_path)
        else:
            self._parse_with_regex(code, rel_path)
    
    def _parse_with_tree_sitter(self, code: str, rel_path: str):
        """Parse using tree-sitter for accurate AST."""
        tree = self.parser.parse(bytes(code, "utf8"))
        root = tree.root_node
        lines = code.split("\n")
        
        # Track calls made by each symbol
        self.symbol_calls: Dict[str, Set[str]] = getattr(self, 'symbol_calls', {})
        # Track class instantiations (e.g., route = APIRoute(...))
        self.symbol_instantiates: Dict[str, Set[str]] = getattr(self, 'symbol_instantiates', {})
        
        def extract_calls_and_instantiations(node, current_symbol=None):
            """Extract function/method calls and class instantiations from a node."""
            calls = set()
            instantiates = set()
            
            def find_calls(n):
                if n.type == "call":
                    # Get the function being called
                    func_node = n.child_by_field_name("function")
                    if func_node:
                        call_text = func_node.text.decode("utf8")
                        calls.add(call_text)
                        
                        # Check if this looks like a class instantiation (PascalCase)
                        # e.g., APIRoute(...), Depends(...), Response(...)
                        base_name = call_text.split(".")[-1]
                        if base_name and base_name[0].isupper():
                            instantiates.add(call_text)
                            
                for child in n.children:
                    find_calls(child)
            
            find_calls(node)
            return calls, instantiates
        
        def visit(node, parent_class=None):
            # Class definitions
            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    class_name = name_node.text.decode("utf8")
                    start_line = node.start_point[0]
                    end_line = node.end_point[0]
                    class_code = "\n".join(lines[start_line:end_line+1])
                    
                    self.symbol_locations[class_name] = {
                        "file": rel_path,
                        "line": start_line + 1,
                        "end_line": end_line + 1,
                        "code": class_code,
                        "type": "class"
                    }
                    self.file_symbols[rel_path].append(class_name)
                    self.graph.add_node(class_name, type="class", file=rel_path)
                    
                    # Parse methods inside class
                    for child in node.children:
                        visit(child, parent_class=class_name)
            
            # Function definitions
            elif node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    func_name = name_node.text.decode("utf8")
                    start_line = node.start_point[0]
                    end_line = node.end_point[0]
                    func_code = "\n".join(lines[start_line:end_line+1])
                    
                    full_name = f"{parent_class}.{func_name}" if parent_class else func_name
                    
                    self.symbol_locations[full_name] = {
                        "file": rel_path,
                        "line": start_line + 1,
                        "end_line": end_line + 1,
                        "code": func_code,
                        "type": "method" if parent_class else "function"
                    }
                    self.file_symbols[rel_path].append(full_name)
                    self.graph.add_node(full_name, type="function", file=rel_path)
                    
                    if parent_class:
                        self.graph.add_edge(parent_class, full_name, relation="contains")
                    
                    # Track what this function calls AND instantiates
                    calls, instantiates = extract_calls_and_instantiations(node, full_name)
                    self.symbol_calls[full_name] = calls
                    self.symbol_instantiates[full_name] = instantiates
            
            # Import statements
            elif node.type == "import_from_statement":
                module_node = node.child_by_field_name("module_name")
                if module_node:
                    module = module_node.text.decode("utf8")
                    # Convert module to potential file path
                    potential_file = module.replace(".", "/") + ".py"
                    self.imports[rel_path].add(potential_file)
                    
                    # Track imported names
                    for child in node.children:
                        if child.type == "dotted_name" and child != module_node:
                            imported_name = child.text.decode("utf8")
                            self.imports[rel_path].add(f"import:{module}.{imported_name}")
            
            elif node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        module = child.text.decode("utf8")
                        potential_file = module.replace(".", "/") + ".py"
                        self.imports[rel_path].add(potential_file)
            
            # Recurse
            for child in node.children:
                if node.type != "class_definition":  # Already handled class children
                    visit(child, parent_class)
        
        visit(root)
    
    def _parse_with_regex(self, code: str, rel_path: str):
        """Fallback regex parsing if tree-sitter unavailable."""
        import re
        lines = code.split("\n")
        
        class_pattern = re.compile(r'^class\s+(\w+)')
        func_pattern = re.compile(r'^(?:    )?def\s+(\w+)')
        import_pattern = re.compile(r'^(?:from\s+(\S+)\s+)?import\s+(\S+)')
        
        current_class = None
        class_start = None
        
        for i, line in enumerate(lines):
            # Class detection
            class_match = class_pattern.match(line)
            if class_match:
                current_class = class_match.group(1)
                class_start = i
                self.symbol_locations[current_class] = {
                    "file": rel_path,
                    "line": i + 1,
                    "code": line,
                    "type": "class"
                }
                self.file_symbols[rel_path].append(current_class)
                self.graph.add_node(current_class, type="class", file=rel_path)
            
            # Function detection
            func_match = func_pattern.match(line)
            if func_match:
                func_name = func_match.group(1)
                if line.startswith("    ") and current_class:
                    full_name = f"{current_class}.{func_name}"
                else:
                    full_name = func_name
                    current_class = None
                
                self.symbol_locations[full_name] = {
                    "file": rel_path,
                    "line": i + 1,
                    "code": line,
                    "type": "method" if "." in full_name else "function"
                }
                self.file_symbols[rel_path].append(full_name)
                self.graph.add_node(full_name, type="function", file=rel_path)
            
            # Import detection
            import_match = import_pattern.match(line)
            if import_match:
                module = import_match.group(1) or import_match.group(2)
                potential_file = module.replace(".", "/") + ".py"
                self.imports[rel_path].add(potential_file)
    
    def _resolve_dependencies(self):
        """Resolve cross-file dependencies."""
        # File-level dependencies
        for file, imported_files in self.imports.items():
            for imp_file in imported_files:
                if imp_file.startswith("import:"):
                    continue  # Skip import tracking entries
                if imp_file in self.file_symbols:
                    # Add edge between files
                    self.graph.add_edge(file, imp_file, relation="imports")
                    
                    # Add edges between symbols
                    for src_symbol in self.file_symbols.get(file, []):
                        for dst_symbol in self.file_symbols.get(imp_file, []):
                            if not self.graph.has_edge(src_symbol, dst_symbol):
                                self.graph.add_edge(src_symbol, dst_symbol, relation="uses")
        
        # Use actual call tracking to create precise edges
        symbol_calls = getattr(self, 'symbol_calls', {})
        for caller, calls in symbol_calls.items():
            for call in calls:
                # Try to resolve call to a symbol
                # Handle patterns: "func", "module.func", "Class.method", "self.method"
                call_parts = call.split(".")
                
                # Direct match
                if call in self.symbol_locations:
                    self.graph.add_edge(caller, call, relation="calls")
                
                # Check for Class.method pattern
                if len(call_parts) == 2:
                    # Could be "module.func" or "Class.method"
                    for sym in self.symbol_locations:
                        if sym.endswith(f".{call_parts[-1]}") or sym == call_parts[-1]:
                            if not self.graph.has_edge(caller, sym):
                                self.graph.add_edge(caller, sym, relation="calls")
                
                # Check base name match (e.g., "Depends" matches "params.Depends")
                base_name = call_parts[-1]
                for sym in self.symbol_locations:
                    sym_base = sym.split(".")[-1]
                    if sym_base == base_name and sym != caller:
                        if not self.graph.has_edge(caller, sym):
                            self.graph.add_edge(caller, sym, relation="may_call")
        
        # NEW: Track class instantiations and link to class methods
        symbol_instantiates = getattr(self, 'symbol_instantiates', {})
        for caller, instantiated_classes in symbol_instantiates.items():
            for class_call in instantiated_classes:
                class_name = class_call.split(".")[-1]  # Get base class name
                
                # Find the class in our symbols
                for sym, info in self.symbol_locations.items():
                    if info.get("type") == "class":
                        sym_base = sym.split(".")[-1] if "." in sym else sym
                        if sym_base == class_name:
                            # Add instantiation edge
                            if not self.graph.has_edge(caller, sym):
                                self.graph.add_edge(caller, sym, relation="instantiates")
                            
                            # CRITICAL: Also link to important methods of the instantiated class
                            # This ensures we see get_route_handler when instantiating APIRoute
                            important_methods = [
                                f"{sym}.__init__",
                                f"{sym}.__call__",
                                f"{sym}.get_route_handler",
                                f"{sym}.handle",
                                f"{sym}.run",
                                f"{sym}.execute",
                                f"{sym}.process",
                            ]
                            for method in important_methods:
                                if method in self.symbol_locations:
                                    if not self.graph.has_edge(caller, method):
                                        self.graph.add_edge(caller, method, relation="instantiates_uses")
        
        # Symbol reference detection (simplified - checks if symbol name appears in other files)
        for symbol, info in self.symbol_locations.items():
            symbol_name = symbol.split(".")[-1]  # Get base name
            for file, symbols in self.file_symbols.items():
                if file != info["file"]:
                    try:
                        file_code = (self.project_root / file).read_text()
                        if symbol_name in file_code:
                            for other_symbol in symbols:
                                if other_symbol != symbol:
                                    self.graph.add_edge(other_symbol, symbol, relation="references")
                    except Exception:
                        pass
    
    def query_blast_radius(self, symbol: str) -> Dict:
        """Get all symbols affected by changing the given symbol."""
        if symbol not in self.graph:
            # Try partial match
            matches = [s for s in self.graph.nodes() if symbol.lower() in s.lower()]
            if matches:
                symbol = matches[0]
            else:
                return {"error": f"Symbol '{symbol}' not found", "suggestions": list(self.graph.nodes())[:5]}
        
        # Get predecessors (things that depend on this symbol)
        dependents = set()
        for pred in self.graph.predecessors(symbol):
            dependents.add(pred)
            # Also get transitive dependents (1 level)
            dependents.update(self.graph.predecessors(pred))
        
        # Get successors (things this symbol depends on / calls)
        dependencies = set()
        instantiated_methods = set()  # NEW: Methods of classes this symbol instantiates
        
        for succ in self.graph.successors(symbol):
            # Check the edge relation
            edge_data = self.graph.get_edge_data(symbol, succ)
            relation = edge_data.get("relation", "") if edge_data else ""
            
            if relation == "instantiates_uses":
                # This is a method of an instantiated class - CRITICAL for understanding behavior
                instantiated_methods.add(succ)
            elif relation == "instantiates":
                # This is the class itself - also get its methods
                dependencies.add(succ)
                # Get all methods of this class
                for method_succ in self.graph.successors(succ):
                    method_edge = self.graph.get_edge_data(succ, method_succ)
                    if method_edge and method_edge.get("relation") == "contains":
                        instantiated_methods.add(method_succ)
            else:
                dependencies.add(succ)
            
            # Also get what those call (1 level deep for multi-file changes)
            dependencies.update(self.graph.successors(succ))
        
        # Get affected files (from both dependents AND dependencies)
        affected_files = set()
        for s in dependents | dependencies | instantiated_methods | {symbol}:
            if s in self.symbol_locations:
                affected_files.add(self.symbol_locations[s]["file"])
        
        return {
            "symbol": symbol,
            "symbol_info": self.symbol_locations.get(symbol, {}),
            "dependents": list(dependents),
            "dependencies": list(dependencies),  # Things symbol calls/uses
            "instantiated_methods": list(instantiated_methods),  # NEW: Methods of instantiated classes
            "affected_files": list(affected_files),
            "blast_radius_size": len(dependents) + len(dependencies) + len(instantiated_methods)
        }
    
    def get_symbol_code(self, symbol: str) -> Optional[str]:
        """Get the source code for a symbol."""
        if symbol in self.symbol_locations:
            return self.symbol_locations[symbol].get("code", "")
        return None
    
    def get_file_contents(self, filepath: str) -> Optional[str]:
        """Get contents of a file."""
        try:
            return (self.project_root / filepath).read_text()
        except Exception:
            return None