"""Locate relevant tests using filesystem scanning."""
import os
import re
from pathlib import Path
from typing import List, Dict, Set


class TestLocator:
    """Find tests related to given symbols/files."""
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.test_dirs = ["tests", "test", "spec", "specs"]
        self.test_patterns = ["test_*.py", "*_test.py", "test*.py"]
    
    def find_tests(self, affected_files: List[str], symbols: List[str]) -> Dict:
        """Find tests related to affected files and symbols."""
        test_files = self._scan_test_files()
        
        relevant_tests = []
        test_fixtures = set()
        test_patterns_found = set()
        
        for test_file in test_files:
            try:
                content = test_file.read_text()
                rel_path = str(test_file.relative_to(self.project_root))
                
                # Check if test imports/references affected files
                is_relevant = False
                matched_symbols = []
                
                for affected in affected_files:
                    module_name = affected.replace("/", ".").replace(".py", "")
                    base_name = Path(affected).stem
                    
                    if module_name in content or base_name in content:
                        is_relevant = True
                        break
                
                # Check for symbol references
                for symbol in symbols:
                    symbol_name = symbol.split(".")[-1]
                    if symbol_name in content:
                        is_relevant = True
                        matched_symbols.append(symbol)
                
                if is_relevant:
                    test_info = self._analyze_test_file(content, rel_path)
                    test_info["matched_symbols"] = matched_symbols
                    relevant_tests.append(test_info)
                    test_fixtures.update(test_info.get("fixtures", []))
                    test_patterns_found.update(test_info.get("patterns", []))
            
            except Exception:
                continue
        
        return {
            "test_files": relevant_tests,
            "total_tests": sum(t.get("test_count", 0) for t in relevant_tests),
            "fixtures_used": list(test_fixtures),
            "patterns_detected": list(test_patterns_found),
            "coverage_hint": self._estimate_coverage(relevant_tests, symbols)
        }
    
    def _scan_test_files(self) -> List[Path]:
        """Scan for all test files."""
        test_files = []
        
        # Check standard test directories
        for test_dir in self.test_dirs:
            dir_path = self.project_root / test_dir
            if dir_path.exists():
                for pattern in self.test_patterns:
                    test_files.extend(dir_path.rglob(pattern))
        
        # Also check root for test files
        for pattern in self.test_patterns:
            test_files.extend(self.project_root.glob(pattern))
        
        return list(set(test_files))
    
    def _analyze_test_file(self, content: str, filepath: str) -> Dict:
        """Analyze a test file for useful information."""
        info = {
            "file": filepath,
            "test_count": 0,
            "fixtures": [],
            "patterns": [],
            "test_functions": []
        }
        
        lines = content.split("\n")
        
        for line in lines:
            # Count test functions
            if re.match(r'\s*def test_\w+', line) or re.match(r'\s*async def test_\w+', line):
                info["test_count"] += 1
                match = re.match(r'\s*(?:async )?def (test_\w+)', line)
                if match:
                    info["test_functions"].append(match.group(1))
            
            # Detect fixtures
            if "@pytest.fixture" in line or "@fixture" in line:
                info["patterns"].append("pytest-fixtures")
            
            if "factory_boy" in content or "Factory(" in content:
                info["fixtures"].append("factory_boy")
                info["patterns"].append("factory-pattern")
            
            if "mock" in line.lower() or "Mock(" in line or "@patch" in line:
                info["patterns"].append("mocking")
            
            if "parametrize" in line:
                info["patterns"].append("parametrized")
        
        # Detect test frameworks
        if "import pytest" in content:
            info["fixtures"].append("pytest")
        if "import unittest" in content:
            info["fixtures"].append("unittest")
        if "from django.test" in content:
            info["fixtures"].append("django-test")
        
        info["fixtures"] = list(set(info["fixtures"]))
        info["patterns"] = list(set(info["patterns"]))
        
        return info
    
    def _estimate_coverage(self, tests: List[Dict], symbols: List[str]) -> str:
        """Estimate test coverage status."""
        if not tests:
            return "NO_TESTS"
        
        total_tests = sum(t.get("test_count", 0) for t in tests)
        matched_count = sum(1 for t in tests if t.get("matched_symbols"))
        
        if total_tests == 0:
            return "NO_TESTS"
        elif matched_count == 0:
            return "INDIRECT_COVERAGE"
        elif total_tests < len(symbols):
            return "PARTIAL_COVERAGE"
        else:
            return "GOOD_COVERAGE"
