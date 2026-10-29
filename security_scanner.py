import ast
import os
import re
from typing import Dict, Any, List, Set
from config import (
    RESTRICTED_IMPORTS,
    RESTRICTED_CALLS,
    MALICIOUS_PATTERNS,
    STDLIB_MODULES,
    IMPORT_TO_PYPI_MAP
)


class SecurityScanner:
    """
    Performs static code analysis using Python's AST (Abstract Syntax Tree)
    and regular expressions to detect malicious operations and extract required pip packages.
    """

    @staticmethod
    def scan_file(file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            return {"is_safe": False, "risk_level": "HIGH", "reasons": ["File does not exist."], "imports": []}

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source_code = f.read()
        except Exception as e:
            return {"is_safe": False, "risk_level": "HIGH", "reasons": [f"Read error: {e}"], "imports": []}

        reasons: List[str] = []
        detected_imports: Set[str] = set()

        # 1. Regex Heuristic Check
        for pattern in MALICIOUS_PATTERNS:
            if re.search(pattern, source_code, re.IGNORECASE):
                reasons.append(f"Matched dangerous signature: `{pattern}`")

        # 2. AST Structural Analysis
        try:
            tree = ast.parse(source_code, filename=file_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_pkg = alias.name.split(".")[0]
                        detected_imports.add(root_pkg)
                        if root_pkg in RESTRICTED_IMPORTS:
                            reasons.append(f"Restricted system module import: `{root_pkg}`")

                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root_pkg = node.module.split(".")[0]
                        detected_imports.add(root_pkg)
                        if root_pkg in RESTRICTED_IMPORTS:
                            reasons.append(f"Restricted system module import: `{root_pkg}`")

                elif isinstance(node, ast.Call):
                    call_name = SecurityScanner._get_call_name(node.func)
                    if call_name in RESTRICTED_CALLS:
                        if call_name in {"eval", "exec"}:
                            reasons.append(f"Dynamic code execution detected: `{call_name}()`")
                        elif call_name in {"os.system", "os.popen", "shutil.rmtree"}:
                            reasons.append(f"Destructive or shell command call: `{call_name}()`")
                        elif call_name.startswith("subprocess."):
                            for kw in node.keywords:
                                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                    reasons.append(f"Unrestricted shell execution: `{call_name}(..., shell=True)`")

        except SyntaxError as se:
            return {
                "is_safe": False,
                "risk_level": "SYNTAX_ERROR",
                "reasons": [f"Python SyntaxError at line {se.lineno}: {se.msg}"],
                "imports": []
            }
        except Exception as e:
            reasons.append(f"AST Parser anomaly: {str(e)}")

        workspace_dir = os.path.dirname(file_path)
        local_py_files = {
            os.path.splitext(f)[0]
            for f in os.listdir(workspace_dir)
            if f.endswith(".py")
        } if os.path.exists(workspace_dir) else set()

        external_packages = []
        for pkg in sorted(detected_imports):
            if pkg not in STDLIB_MODULES and pkg not in local_py_files and not pkg.startswith("_"):
                pypi_name = IMPORT_TO_PYPI_MAP.get(pkg, pkg)
                if pypi_name not in external_packages:
                    external_packages.append(pypi_name)

        is_safe = len(reasons) == 0
        risk_level = "LOW (CLEAN)" if is_safe else "CRITICAL"

        return {
            "is_safe": is_safe,
            "risk_level": risk_level,
            "reasons": reasons,
            "imports": external_packages
        }

    @staticmethod
    def scan_directory(directory_path: str) -> Dict[str, Any]:
        """Scans all .py files inside a cloned GitHub repository or extracted .zip directory."""
        aggregated_reasons = []
        aggregated_imports = set()
        syntax_errors = []

        for root, _, files in os.walk(directory_path):
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, directory_path)
                    res = SecurityScanner.scan_file(full_path)
                    if res["risk_level"] == "SYNTAX_ERROR":
                        syntax_errors.extend([f"[{rel_path}] {r}" for r in res["reasons"]])
                    elif not res["is_safe"]:
                        aggregated_reasons.extend([f"[{rel_path}] {r}" for r in res["reasons"]])
                    for imp in res.get("imports", []):
                        aggregated_imports.add(imp)

        is_safe = len(aggregated_reasons) == 0 and len(syntax_errors) == 0
        risk = "LOW (CLEAN)"
        if syntax_errors:
            risk = "SYNTAX_ERROR"
        if aggregated_reasons:
            risk = "CRITICAL"

        return {
            "is_safe": is_safe,
            "risk_level": risk,
            "reasons": aggregated_reasons + syntax_errors,
            "imports": sorted(list(aggregated_imports))
        }

    @staticmethod
    def _get_call_name(func_node: ast.AST) -> str:
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            value_name = SecurityScanner._get_call_name(func_node.value)
            return f"{value_name}.{func_node.attr}" if value_name else func_node.attr
        return ""
