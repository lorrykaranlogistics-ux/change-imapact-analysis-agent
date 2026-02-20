from collections import defaultdict, deque
from typing import Dict, List, Set

import networkx as nx


class GraphEngine:
    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    def build_graph(self, dep_map: Dict[str, List[str]], services: Set[str]) -> nx.DiGraph:
        for service in services:
            self.graph.add_node(service)

        for file_path, imports in dep_map.items():
            source_service = file_path.split("/")[0]
            for imp in imports:
                for candidate in services:
                    if candidate in imp and candidate != source_service:
                        self.graph.add_edge(source_service, candidate)
                if "shared" in imp and source_service != "shared" and "shared" in services:
                    self.graph.add_edge(source_service, "shared")

        return self.graph

    def analyze_impact(self, changed_files: List[str]) -> Dict:
        changed_services = set(path.split("/")[0] for path in changed_files)

        impacted: Set[str] = set(changed_services)
        upstream: Set[str] = set()
        downstream: Set[str] = set()
        cross_map = defaultdict(list)

        max_depth = 0
        for svc in changed_services:
            if svc not in self.graph:
                continue

            ancestors = nx.ancestors(self.graph, svc)
            descendants = nx.descendants(self.graph, svc)
            upstream.update(ancestors)
            downstream.update(descendants)
            impacted.update(ancestors)
            impacted.update(descendants)

            for other in ancestors.union(descendants):
                cross_map[svc].append(other)

            # BFS depth around changed service
            queue = deque([(svc, 0)])
            seen = {svc}
            while queue:
                node, depth = queue.popleft()
                max_depth = max(max_depth, depth)
                neighbors = set(self.graph.successors(node)).union(self.graph.predecessors(node))
                for n in neighbors:
                    if n not in seen:
                        seen.add(n)
                        queue.append((n, depth + 1))

        return {
            "impacted_services": sorted(list(impacted)),
            "dependency_depth": max_depth,
            "upstream_dependencies": sorted(list(upstream)),
            "downstream_dependencies": sorted(list(downstream)),
            "cross_service_impacts": {k: sorted(v) for k, v in cross_map.items()},
        }
