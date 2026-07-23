export type ReactFlowEngineConfig = {
  defaultViewport: {
    x: number;
    y: number;
    zoom: number;
  };
  fitViewOptions: {
    padding: number;
    minZoom: number;
    maxZoom: number;
    includeHiddenNodes: boolean;
  };
  snapToGrid: boolean;
  snapGrid: readonly [number, number];
  panOnDrag: boolean;
  panOnScroll: boolean;
  zoomOnScroll: boolean;
  zoomOnPinch: boolean;
  nodesDraggable: boolean;
  nodesConnectable: boolean;
  elementsSelectable: boolean;
  elevateNodesOnSelect: boolean;
  autoPanOnNodeDrag: boolean;
};

/**
 * Centralized defaults for the future React Flow graph engine.
 * Keeping these values in one place makes the eventual migration from the
 * canvas-based dependency graph straightforward and keeps view behavior stable.
 */
export const reactFlowEngineConfig: ReactFlowEngineConfig = {
  defaultViewport: {
    x: 0,
    y: 0,
    zoom: 1,
  },
  fitViewOptions: {
    padding: 0.2,
    minZoom: 0.6,
    maxZoom: 1.4,
    includeHiddenNodes: false,
  },
  snapToGrid: true,
  snapGrid: [16, 16] as const,
  panOnDrag: true,
  panOnScroll: false,
  zoomOnScroll: true,
  zoomOnPinch: true,
  nodesDraggable: false,
  nodesConnectable: false,
  elementsSelectable: false,
  elevateNodesOnSelect: false,
  autoPanOnNodeDrag: false,
};

