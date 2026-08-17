import React, { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { useTopology } from "../../hooks/useTopology";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { AlertTriangle, RefreshCw, Loader2, Network } from "lucide-react";
import { TopologyNode } from "../../types/topology";
import "./topology.css";

export interface TopologyGraphProps {
  mode?: "compact" | "full" | "root-cause-focus";
  onNodeSelect?: (nodeId: string | null) => void;
  selectedNodeId?: string | null;
  showLowSeverity?: boolean;
}

export function ServiceTopologyGraph({ 
  mode = "full", 
  onNodeSelect, 
  selectedNodeId: controlledSelectedId, 
  showLowSeverity = true 
}: TopologyGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const { nodes: initialNodes, edges: initialEdges, updatedAt, isLoading, error, refresh } = useTopology();
  const { activeTrackingLoops } = useTelemetryStream();

  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(null);
  const selectedNodeId = controlledSelectedId ?? internalSelectedId;
  const [statusFilter, setStatusFilter] = useState<"ALL" | "CRITICAL" | "DEGRADED" | "HEALTHY">("ALL");

  const overlayMap = useMemo(() => {
    const map = new Map<string, { status: string; isRoot: boolean }>();
    
    for (const loop of activeTrackingLoops) {
      if (!showLowSeverity && loop.severity === "low") continue;
      
      if (loop.suspected_root_service) {
        const severity = loop.severity === "critical" || loop.severity === "high" ? "critical" : "degraded";
        map.set(loop.suspected_root_service, {
          status: severity,
          isRoot: true,
        });
      }

      const blastData = loop.blast_radius as any;
      if (blastData && Array.isArray(blastData)) {
        for (const brNode of blastData) {
          if (typeof brNode.service_name !== "string") continue;
          
          const existing = map.get(brNode.service_name);
          const isRoot = brNode.impact_classification === "root";
          const severity = loop.severity === "critical" || loop.severity === "high" ? "critical" : "degraded";

          if (!existing || severity === "critical" || (severity === "degraded" && existing.status !== "critical")) {
            map.set(brNode.service_name, {
              status: severity,
              isRoot: isRoot || (existing?.isRoot ?? false),
            });
          }
        }
      }
    }
    return map;
  }, [activeTrackingLoops, showLowSeverity]);

  useEffect(() => {
    if (!containerRef.current) return;

    const filteredSourceNodes = initialNodes.filter(node => {
      if (statusFilter === "ALL") return true;
      const overlay = overlayMap.get(node.id);
      const status = overlay ? overlay.status.toUpperCase() : "HEALTHY";
      return status === statusFilter;
    });

    const activeNodeIds = new Set(filteredSourceNodes.map(n => n.id));

    const cyNodes = filteredSourceNodes.map(node => {
      const overlay = overlayMap.get(node.id);
      const status = overlay ? overlay.status : 'healthy';
      return {
        data: {
          id: node.id,
          label: node.name,
          type: node.type,
          status: status,
          isRoot: overlay?.isRoot || false,
          metrics: node.metrics,
        },
      };
    });

    const cyEdges = initialEdges
      .filter(edge => activeNodeIds.has(edge.source) && activeNodeIds.has(edge.target))
      .map(edge => {
        const srcOverlay = overlayMap.get(edge.source);
        const tgtOverlay = overlayMap.get(edge.target);
        
        let status = 'healthy';
        if (srcOverlay?.status === 'critical' || tgtOverlay?.status === 'critical') status = 'critical';
        else if (srcOverlay?.status === 'degraded' || tgtOverlay?.status === 'degraded') status = 'degraded';
        
        return {
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            status,
            latency: edge.avg_latency_ms
          }
        };
      });

    if (!cyRef.current) {
      cyRef.current = cytoscape({
        container: containerRef.current,
        elements: { nodes: cyNodes, edges: cyEdges },
        style: [
          {
            selector: 'node',
            style: {
              'background-color': (ele) => {
                const status = ele.data('status');
                if (status === 'critical') return '#ef4444';
                if (status === 'degraded') return '#f59e0b';
                return '#388bfd';
              },
              'label': 'data(label)',
              'color': '#e6edf3',
              'font-size': '11px',
              'font-family': 'Inter, sans-serif',
              'text-valign': 'bottom',
              'text-margin-y': 6,
              'width': 32,
              'height': 32,
              'border-width': 2,
              'border-color': (ele) => {
                const isRoot = ele.data('isRoot');
                if (isRoot) return '#fff';
                return '#21262d';
              }
            }
          },
          {
            selector: 'node:selected',
            style: {
              'border-color': '#fff',
              'border-width': 3,
            }
          },
          {
            selector: 'edge',
            style: {
              'width': 1.5,
              'line-color': (ele) => {
                const status = ele.data('status');
                if (status === 'critical') return '#ef4444';
                if (status === 'degraded') return '#f59e0b';
                return '#30363d';
              },
              'target-arrow-color': (ele) => {
                const status = ele.data('status');
                if (status === 'critical') return '#ef4444';
                if (status === 'degraded') return '#f59e0b';
                return '#30363d';
              },
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'opacity': 0.8
            }
          }
        ],
        layout: {
          name: 'cose',
          animate: true,
          randomize: false,
          componentSpacing: 80,
          nodeOverlap: 10,
          idealEdgeLength: (edge: any) => 80,
          edgeElasticity: (edge: any) => 100,
          nestingFactor: 5,
          gravity: 80,
          numIter: 1000,
        },
        minZoom: 0.2,
        maxZoom: 3,
        wheelSensitivity: 0.1
      });

      cyRef.current.on('tap', 'node', (evt) => {
        const node = evt.target;
        const id = node.id();
        const next = id === selectedNodeId ? null : id;
        setInternalSelectedId(next);
        onNodeSelect?.(next);
      });
      
      cyRef.current.on('tap', (evt) => {
        if (evt.target === cyRef.current) {
          setInternalSelectedId(null);
          onNodeSelect?.(null);
        }
      });
    } else {
      const cy = cyRef.current;
      let needsLayout = false;
      
      cy.batch(() => {
        const existingNodeIds = new Set(cy.nodes().map(n => n.id()));
        cyNodes.forEach(n => {
          if (existingNodeIds.has(n.data.id)) {
            cy.getElementById(n.data.id).data(n.data);
            existingNodeIds.delete(n.data.id);
          } else {
            cy.add({ group: 'nodes', data: n.data });
            needsLayout = true;
          }
        });
        if (existingNodeIds.size > 0) {
          existingNodeIds.forEach(id => cy.getElementById(id).remove());
          needsLayout = true;
        }

        const existingEdgeIds = new Set(cy.edges().map(e => e.id()));
        cyEdges.forEach(e => {
          if (existingEdgeIds.has(e.data.id)) {
            cy.getElementById(e.data.id).data(e.data);
            existingEdgeIds.delete(e.data.id);
          } else {
            cy.add({ group: 'edges', data: e.data });
            needsLayout = true;
          }
        });
        if (existingEdgeIds.size > 0) {
          existingEdgeIds.forEach(id => cy.getElementById(id).remove());
          needsLayout = true;
        }
      });

      if (needsLayout) {
        cy.layout({
          name: 'cose',
          animate: true,
          randomize: false,
          fit: false,
          componentSpacing: 80,
          nodeOverlap: 10,
          idealEdgeLength: (edge: any) => 80,
          edgeElasticity: (edge: any) => 100,
          nestingFactor: 5,
          gravity: 80,
          numIter: 1000,
        }).run();
      }
    }
  }, [initialNodes, initialEdges, overlayMap, statusFilter]);

  // Sync selectedNodeId prop with Cytoscape
  useEffect(() => {
    if (!cyRef.current) return;
    const cy = cyRef.current;
    cy.nodes().unselect();
    if (selectedNodeId) {
      cy.getElementById(selectedNodeId).select();
    }
  }, [selectedNodeId]);
  
  // Cleanup
  useEffect(() => {
    return () => {
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
    };
  }, []);

  const blastRadiusCount = useMemo(() => {
    let count = 0;
    for (const loop of activeTrackingLoops) {
      if (!showLowSeverity && loop.severity === "low") continue;
      const blastData = loop.blast_radius as any;
      if (blastData && Array.isArray(blastData)) {
        count += blastData.length;
      } else if (loop.suspected_root_service) {
        count += 1;
      }
    }
    return count;
  }, [activeTrackingLoops, showLowSeverity]);

  if (error && initialNodes.length === 0) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-[#161b22] rounded-xl border border-[#21262d] p-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <AlertTriangle className="w-8 h-8 text-[#ef4444]" />
          <span className="text-[#e6edf3] font-semibold text-sm">Failed to load service topology</span>
          <span className="text-[#8b949e] text-xs max-w-sm">{error}</span>
          <button 
            onClick={refresh} 
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#21262d] text-[#e6edf3] hover:bg-[#30363d] text-xs font-semibold transition-colors mt-2"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Retry Connection
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative w-full h-full bg-[#0d1117] rounded-xl border border-[#21262d] overflow-hidden group">
      {/* Header bar */}
      <div className="absolute top-3 left-4 right-4 z-10 flex flex-wrap items-center justify-between gap-2 pointer-events-none">
        <div className="flex items-center gap-2 pointer-events-auto bg-[#161b22]/95 backdrop-blur-md px-3 py-1.5 rounded-lg border border-[#21262d] shadow-md">
          <Network className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">
            {mode === "root-cause-focus" ? "Service Dependency Graph" : "Service Topology"}
          </span>
          {blastRadiusCount > 0 && (
            <span className="ml-2 px-2 py-0.5 rounded-full text-white text-[10px] font-bold bg-[#da3633] animate-pulse">
              {blastRadiusCount} Degraded
            </span>
          )}
        </div>

        {mode !== "compact" && (
          <div className="flex items-center gap-1 pointer-events-auto bg-[#161b22]/95 backdrop-blur-md p-1 rounded-lg border border-[#21262d] shadow-md">
            {(["ALL", "CRITICAL", "DEGRADED", "HEALTHY"] as const).map((filterKey) => {
              const active = statusFilter === filterKey;
              return (
                <button
                  key={filterKey}
                  onClick={() => setStatusFilter(filterKey)}
                  className={`px-2 py-0.5 rounded text-[9px] font-bold transition-all ${
                    active 
                      ? "bg-[#388bfd] text-white" 
                      : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]"
                  }`}
                >
                  {filterKey}
                </button>
              );
            })}
          </div>
        )}
      </div>
      
      <div className="absolute top-3 right-4 z-10 pointer-events-auto bg-[#161b22]/95 backdrop-blur-md rounded-lg border border-[#21262d] flex items-center p-1 shadow-md gap-2">
        {updatedAt && (
          <span className="text-[#7d8590] px-2 text-[10px] font-mono hidden md:inline">
            Updated {new Date(updatedAt).toLocaleTimeString()}
          </span>
        )}
        <button
          onClick={refresh}
          disabled={isLoading}
          title="Refresh topology"
          className="p-1.5 rounded hover:bg-[#21262d] text-[#8b949e] hover:text-[#e6edf3] transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin text-[#388bfd]" : ""}`} />
        </button>
      </div>
      
      {initialNodes.length === 0 && isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0d1117] z-10 pointer-events-none">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="w-8 h-8 text-[#388bfd] animate-spin" />
            <span className="text-[#e6edf3] font-semibold text-sm">Mapping Service Dependencies...</span>
            <div className="w-48 h-1 bg-[#21262d] rounded-full overflow-hidden">
               <div className="h-full bg-[#388bfd] animate-pulse rounded-full w-2/3" />
            </div>
          </div>
        </div>
      )}
      
      <div ref={containerRef} className="w-full h-full min-h-[400px]" style={{ touchAction: 'none' }} />
    </div>
  );
}
