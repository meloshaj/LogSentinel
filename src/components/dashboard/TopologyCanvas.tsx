import { ServiceTopologyGraph } from "../topology/ServiceTopologyGraph";

/**
 * Compatibility wrapper for the legacy dashboard canvas entry point.
 *
 * The authoritative topology renderer owns its data state and does not fill
 * missing nodes with synthetic "normal" values. Keeping this export avoids
 * breaking older consumers while routing them through the same truthful graph.
 */
export function TopologyCanvas() {
  return <ServiceTopologyGraph mode="full" />;
}
