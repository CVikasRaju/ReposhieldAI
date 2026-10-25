import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";
import { useCallback, useEffect, useRef, useState } from "react";

import { NODE_STATE_META, nodeLabel } from "../lib/format";
import { useStore } from "../store";
import type { GraphPayload, NodeState } from "../types";

function ancestorsOf(id: string, parentOf: Map<string, string | null>): string[] {
  const chain: string[] = [];
  let current = parentOf.get(id) ?? null;
  while (current) {
    chain.push(current);
    current = parentOf.get(current) ?? null;
  }
  return chain;
}

function buildElements(
  graph: GraphPayload,
  collapsed: Set<string>,
  extraExpanded: Set<string>,
): ElementDefinition[] {
  const parentOf = new Map<string, string | null>(
    graph.nodes.map((node) => [node.id, node.parent ?? null]),
  );
  const dirIds = new Set(graph.nodes.filter((node) => node.type === "dir").map((node) => node.id));

  const autoExpanded = new Set<string>();
  for (const node of graph.nodes) {
    if (node.state === "unvisited") continue;
    for (const ancestor of ancestorsOf(node.id, parentOf)) {
      if (dirIds.has(ancestor)) autoExpanded.add(ancestor);
    }
  }

  const expanded = new Set([...autoExpanded, ...extraExpanded]);
  for (const id of collapsed) expanded.delete(id);

  const isVisible = (id: string) => ancestorsOf(id, parentOf).every((a) => expanded.has(a));

  const visibleNodes = graph.nodes.filter((node) => isVisible(node.id));
  const visibleIds = new Set(visibleNodes.map((node) => node.id));

  // Hierarchy is drawn with edges only: compound parents distort every layout here.
  const elements: ElementDefinition[] = visibleNodes.map((node) => ({
    group: "nodes",
    data: {
      id: node.id,
      label: nodeLabel(node),
      type: node.type,
      state: node.state,
    },
    classes: `state-${node.state}`,
  }));

  for (const edge of graph.edges) {
    if (visibleIds.has(edge.source) && visibleIds.has(edge.target)) {
      elements.push({
        group: "edges",
        data: { id: `${edge.source}->${edge.target}`, source: edge.source, target: edge.target },
      });
    }
  }
  return elements;
}

export function KnowledgeGraph() {
  const graph = useStore((state) => state.graph);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const collapsedRef = useRef<Set<string>>(new Set());
  const extraExpandedRef = useRef<Set<string>>(new Set());
  const signatureRef = useRef<string>("");
  const stateSignatureRef = useRef<string>("");
  const graphRef = useRef<GraphPayload | null>(null);
  const [version, setVersion] = useState(0);

  graphRef.current = graph;

  const rerender = useCallback(() => setVersion((value) => value + 1), []);

  const fitGraph = useCallback((cy: Core, attempts = 8) => {
    // The panel is laid out after hydration, so retry until it has a real height.
    const run = (left: number) => {
      requestAnimationFrame(() => {
        if (cy.destroyed() || !containerRef.current) return;
        if (containerRef.current.clientHeight === 0 && left > 0) {
          run(left - 1);
          return;
        }
        cy.resize();
        if (cy.elements().length > 0) cy.fit(cy.elements(), 24);
      });
    };
    run(attempts);
  }, []);

  const syncGraph = useCallback(
    (cy: Core, payload: GraphPayload) => {
      const elements = buildElements(payload, collapsedRef.current, extraExpandedRef.current);
      const structureSignature = elements
        .map((element) => `${element.group}:${element.data.id}`)
        .join("|");
      const stateSignature = elements.map((element) => element.classes).join("|");
      const structureChanged = structureSignature !== signatureRef.current;
      const stateChanged = stateSignature !== stateSignatureRef.current;
      signatureRef.current = structureSignature;
      stateSignatureRef.current = stateSignature;
      if (structureChanged) {
        // Re-adding nodes resets their positions, so only rebuild when the shape changed.
        cy.batch(() => {
          cy.elements().remove();
          cy.add(elements);
        });
        cy.layout({
          name: "cose",
          animate: false,
          padding: 24,
          nodeRepulsion: () => 6400,
          idealEdgeLength: () => 80,
        }).run();
        fitGraph(cy);
      } else if (stateChanged) {
        for (const element of elements) {
          const node = cy.getElementById(String(element.data.id));
          if (node.nonempty()) {
            node.classes(String(element.classes));
            node.data("state", element.data.state);
          }
        }
      }
    },
    [fitGraph],
  );

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
  {
    selector: "node",
    style: {
      "background-color": "#cbd5e1",
      "background-opacity": 0.95,
      "border-width": 2,
      "border-color": "#94a3b8",
      label: "data(label)",
      color: "#1e1035",
      "font-weight": 600,
      "text-outline-color": "#ffffff",
      "text-outline-width": 2,
      "font-size": 10,
      "text-valign": "bottom",
      "text-margin-y": 4,
      "text-wrap": "ellipsis",
      "text-max-width": "96px",
      width: 22,
      height: 22,
      "underlay-color": "#ef4444",
      "underlay-opacity": 0,
      "underlay-padding": 6,
    },
  },
  {
    selector: "node[type='dir']",
    style: { shape: "round-rectangle", width: 30, height: 24, "background-color": "#e2e8f0" },
  },
  { selector: "node[type='database']", style: { shape: "ellipse", width: 30, height: 30 } },
  { selector: "node[type='table']", style: { shape: "diamond", width: 26, height: 26 } },
  { selector: "node[type='host']", style: { shape: "hexagon", width: 30, height: 30 } },
  {
    selector: "node.state-accessed",
    style: { "background-color": "#10b981", "border-color": "#059669" },
  },
  {
    selector: "node.state-masked",
    style: { "background-color": "#f59e0b", "border-color": "#d97706" },
  },
  {
    selector: "node.state-held",
    style: {
      "background-color": "#3b82f6",
      "border-color": "#2563eb",
      "underlay-color": "#f59e0b",
    },
  },
  {
    selector: "node.state-blocked",
    style: {
      "background-color": "#ef4444",
      "border-color": "#dc2626",
      "border-width": 3,
      "underlay-color": "#ef4444",
    },
  },
  {
    selector: "node:selected",
    style: { "border-color": "#2a0845", "border-width": 3 },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#94a3b8",
      "target-arrow-color": "#94a3b8",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "arrow-scale": 0.6,
    },
  },
],
      minZoom: 0.3,
      maxZoom: 2.5,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (event) => {
      useStore.getState().setFilters({ nodeId: event.target.id() });
    });
    cy.on("dbltap", "node", (event) => {
      const node = event.target;
      if (node.data("type") !== "dir") return;
      const id = String(node.id());
      const collapsed = collapsedRef.current;
      const extra = extraExpandedRef.current;
      if (collapsed.has(id)) {
        collapsed.delete(id);
        extra.add(id);
      } else if (!extra.has(id)) {
        // Never expanded by the user: collapse it.
        collapsed.add(id);
      } else {
        extra.delete(id);
      }
      rerender();
    });

    const resizer = new ResizeObserver(() => cy.resize());
    resizer.observe(containerRef.current);

    // A fresh instance must lay out from scratch even if the payload is unchanged.
    signatureRef.current = "";
    stateSignatureRef.current = "";
    if (graphRef.current) syncGraph(cy, graphRef.current);

    return () => {
      resizer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, [rerender, syncGraph]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;
    syncGraph(cy, graph);
  }, [graph, version, syncGraph]);

  useEffect(() => {
    let heldPhase = false;
    const heldTimer = window.setInterval(() => {
      heldPhase = !heldPhase;
      cyRef.current
        ?.nodes(".state-held")
        .style("underlay-opacity", heldPhase ? 0.35 : 0.08);
    }, 700);
    let blockedPhase = false;
    const blockedTimer = window.setInterval(() => {
      blockedPhase = !blockedPhase;
      cyRef.current
        ?.nodes(".state-blocked")
        .style("underlay-opacity", blockedPhase ? 0.55 : 0.1);
    }, 360);
    return () => {
      window.clearInterval(heldTimer);
      window.clearInterval(blockedTimer);
    };
  }, []);

  const states: NodeState[] = ["unvisited", "accessed", "masked", "held", "blocked"];

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border border-slate-300 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 bg-slate-50/70 rounded-t-2xl">
        <h2 className="text-sm font-bold text-[#2a0845]">Knowledge graph</h2>
        <span className="text-[11px] font-medium text-slate-500">What the AI thinks it knows</span>
      </header>
      <div className="relative min-h-[260px] flex-1">
        {/* Cytoscape forces its container to position: relative, so size it directly. */}
        <div ref={containerRef} className="h-full w-full" aria-label="Workspace knowledge graph" />
        {graph && graph.nodes.length === 0 ? (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-slate-500">
            Waiting for the agent to touch the workspace.
          </p>
        ) : null}
      </div>
      <footer className="border-t border-slate-200 px-4 py-2.5 bg-slate-50/70 rounded-b-2xl">
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {states.map((state) => {
            const meta = NODE_STATE_META[state];
            return (
              <li key={state} className="flex items-center gap-1.5 text-[11px] text-slate-600 font-semibold">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: meta.color }}
                />
                <span aria-hidden>{meta.icon}</span>
                <span>{meta.label}</span>
              </li>
            );
          })}
        </ul>
        <p className="mt-1 text-[11px] text-slate-500">
          Green: the agent has seen it. Grey: it has not. Double-click a folder to collapse it.
        </p>
      </footer>
    </section>
  );
}
