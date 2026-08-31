import type { BlastRadiusNode, TrackingLoopEvent } from "../types/telemetryEvents";

/** Resolve a real service identifier from graph evidence, never a synthetic label. */
export function resolveRootService(loop: TrackingLoopEvent): string | null {
  const declared = loop.suspected_root_service?.trim();
  if (declared && declared !== "multiple-services") return declared;

  const nodes = Array.isArray(loop.blast_radius) ? loop.blast_radius : [];
  return (
    nodes.find((node: BlastRadiusNode) => node.impact_classification === "root")
      ?.service_name || nodes[0]?.service_name || null
  );
}

export function affectedServiceIds(loop: TrackingLoopEvent): string[] {
  return Array.from(
    new Set(
      (Array.isArray(loop.blast_radius) ? loop.blast_radius : [])
        .map((node) => node.service_name)
        .filter((service): service is string => Boolean(service)),
    ),
  );
}
