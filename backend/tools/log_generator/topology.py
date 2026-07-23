"""Service topology and dependency graph built on ``networkx``.

The ``ServiceTopology`` class loads a list of ``ServiceNode`` configs into a
Directed Acyclic Graph (DAG) that represents the call-chain between
microservices.  It exposes methods to query upstream/downstream neighbours
and to generate realistic execution trace paths.
"""

from __future__ import annotations

import uuid
from typing import Any

import networkx as nx

from .config import ServiceNode


class ServiceTopology:
    """DAG-backed dependency graph of ``ServiceNode`` configurations.

    Parameters
    ----------
    nodes:
        List of ``ServiceNode`` instances.  Each node's ``dependencies``
        field lists the service names it calls *downstream*.

    Raises
    ------
    ValueError
        If a dependency references a service name that is not in ``nodes``.
    nx.NetworkXUnfeasible
        If the declared dependencies form a cycle.
    """

    def __init__(self, nodes: list[ServiceNode]) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._nodes: dict[str, ServiceNode] = {}

        for node in nodes:
            self._nodes[node.service_name] = node
            self._graph.add_node(node.service_name, **node.model_dump())

        # Edges point from caller → callee (upstream → downstream).
        for node in nodes:
            for dep in node.dependencies:
                if dep not in self._nodes:
                    raise ValueError(
                        f"Service '{node.service_name}' declares dependency "
                        f"'{dep}' which is not defined in the topology."
                    )
                self._graph.add_edge(node.service_name, dep)

        if not nx.is_directed_acyclic_graph(self._graph):
            raise nx.NetworkXUnfeasible(
                "The declared service dependencies form a cycle. "
                "Only DAGs are supported."
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_node(self, service_name: str) -> ServiceNode:
        """Return the ``ServiceNode`` for a given service name."""
        return self._nodes[service_name]

    @property
    def service_names(self) -> list[str]:
        """Return all service names in topological order."""
        return list(nx.topological_sort(self._graph))

    def upstream_of(self, service_name: str) -> list[str]:
        """Return the immediate *callers* of the given service."""
        return list(self._graph.predecessors(service_name))

    def downstream_of(self, service_name: str) -> list[str]:
        """Return the immediate *callees* of the given service."""
        return list(self._graph.successors(service_name))

    def root_services(self) -> list[str]:
        """Return service names with no incoming edges (entry points)."""
        return [n for n in self._graph.nodes if self._graph.in_degree(n) == 0]

    def leaf_services(self) -> list[str]:
        """Return service names with no outgoing edges (terminal services)."""
        return [n for n in self._graph.nodes if self._graph.out_degree(n) == 0]

    # ------------------------------------------------------------------
    # Trace generation
    # ------------------------------------------------------------------

    def generate_trace_path(
        self,
        entry_service: str | None = None,
    ) -> tuple[str, list[str]]:
        """Generate a valid execution trace path through the topology.

        Starting from ``entry_service`` (or a random root if ``None``),
        walk every reachable downstream service in topological order.

        Returns
        -------
        tuple[str, list[str]]
            ``(correlation_id, [service_name, …])`` — the correlation ID
            is a freshly minted UUID shared by every service in the trace.
        """
        if entry_service is None:
            roots = self.root_services()
            if not roots:
                roots = list(self._nodes.keys())
            entry_service = roots[0]

        # BFS / topological traversal of the sub-DAG reachable from entry.
        reachable = nx.descendants(self._graph, entry_service) | {entry_service}
        sub = self._graph.subgraph(reachable)
        ordered_path: list[str] = list(nx.topological_sort(sub))

        correlation_id = str(uuid.uuid4())
        return correlation_id, ordered_path

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a JSON-friendly summary of the topology."""
        return {
            "node_count": self._graph.number_of_nodes(),
            "edge_count": self._graph.number_of_edges(),
            "roots": self.root_services(),
            "leaves": self.leaf_services(),
            "topological_order": self.service_names,
            "edges": [
                {"from": u, "to": v} for u, v in self._graph.edges
            ],
        }
