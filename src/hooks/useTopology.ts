import { useState, useEffect } from 'react';

export interface TopologyNode {
  id: string;
  service: string;
  event_count: number;
  transaction_count: number;
  first_seen: string;
  last_seen: string;
  minimum_start_offset_ms: number;
  maximum_start_offset_ms: number;
  average_start_offset_ms: number;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  transition_count: number;
  transaction_count: number;
  first_seen: string;
  last_seen: string;
  minimum_delay_ms: number | null;
  maximum_delay_ms: number | null;
  average_delay_ms: number | null;
  span_evidence_count: number;
  temporal_evidence_count: number;
  target_hint_evidence_count: number;
}

export interface TopologyPayload {
  generated_at: string | null;
  node_count: number;
  edge_count: number;
  transaction_count: number;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export function useTopology(pollIntervalMs = 5000) {
  const [topology, setTopology] = useState<TopologyPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function fetchTopology() {
      try {
        const baseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
        const res = await fetch(`${baseUrl}/api/v1/topology`);
        if (!res.ok) {
          throw new Error(`Failed to fetch topology: ${res.status} ${res.statusText}`);
        }
        const data = await res.json();
        if (mounted) {
          setTopology(data);
          setError(null);
        }
      } catch (err: any) {
        if (mounted) {
          setError(err.message);
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    }

    fetchTopology();
    const interval = setInterval(fetchTopology, pollIntervalMs);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [pollIntervalMs]);

  return { topology, loading, error };
}
