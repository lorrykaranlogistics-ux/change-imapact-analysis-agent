import os
import re
from typing import Dict, List, Set, Tuple

IMPORT_PATTERNS = [
    re.compile(r"require\(['\"]([^'\"]+)['\"]\)"),
    re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
]
SERVICE_CALL_PATTERN = re.compile(r"http://([a-z0-9-]+):\d+", re.IGNORECASE)


class DependencyParser:
    def parse_project(self, root: str) -> Tuple[Dict[str, List[str]], Set[str]]:
        dep_map: Dict[str, List[str]] = {}
        services: Set[str] = set()

        for base, _, files in os.walk(root):
            if "node_modules" in base:
                continue
            for file in files:
                if not file.endswith((".js", ".ts")):
                    continue
                full_path = os.path.join(base, file)
                rel_path = os.path.relpath(full_path, root)
                service = rel_path.split(os.sep)[0]
                if service in {"sample-pr", ".github", "k8s"}:
                    continue
                services.add(service)
                imports = self._extract_imports_and_calls(full_path)
                dep_map[rel_path] = imports

        return dep_map, services

    def _extract_imports_and_calls(self, file_path: str) -> List[str]:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        imports: List[str] = []
        for pattern in IMPORT_PATTERNS:
            imports.extend(pattern.findall(content))
        for svc in SERVICE_CALL_PATTERN.findall(content):
            imports.append(f"service://{svc.lower()}")
        return imports
