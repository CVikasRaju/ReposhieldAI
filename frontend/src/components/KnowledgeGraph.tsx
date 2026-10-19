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

  const elements: ElementDefinition[] = visibleNodes.map((node) => ({
    group: "nodes",
    data: {
      id: node.id,
      label: nodeLabel(node),
      type: node.type,
      parent: node.parent && visibleIds.has(node.parent) ? node.parent : undefined,
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
  const [version, setVersion] = useState(0);

  const rerender = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
  {
    selector: "node",
    style: {
      "background-color": "#3f3f46",
      "background-opacity": 0.9,
      "border-width": 2,
      "border-color": "#52525b",
      label: "data(label)",
      color: "#e4e4e7",
      "font-size": 9,
      "text-valign": "bottom",
      "text-margin-y": 3,
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
    style: { shape: "round-rectangle", width: 30, height: 24, "background-opacity": 0.6 },
  },
  { selector: "node[type='database']", style: { shape: "ellipse", width: 30, height: 30 } },
  { selector: "node[type='table']", style: { shape: "diamond", width: 26, height: 26 } },
  { selector: "node[type='host']", style: { shape: "hexagon", width: 30, height: 30 } },
  {
    selector: "node.state-accessed",
    style: { "background-color": "#34d399", "border-color": "#6ee7b7" },
  },
  {
    selector: "node.state-masked",
    style: { "background-color": "#fbbf24", "border-color": "#fcd34d" },
  },
  {
    selector: "node.state-held",
    style: {
      "background-color": "#60a5fa",
      "border-color": "#93c5fd",
      "underlay-color": "#60a5fa",
    },
  },
  {
    selector: "node.state-blocked",
    style: {
      "background-color": "#f87171",
      "border-color": "#fca5a5",
      "border-width": 3,
      "underlay-color": "#ef4444",
    },
  },
  {
    selector: "node:selected",
    style: { "border-color": "#a7f3d0", "border-width": 3 },
  },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "#3f3f46",
      "target-arrow-color": "#3f3f46",
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

    return () => {
      resizer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, [rerender]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;
    const elements = buildElements(graph, collapsedRef.current, extraExpandedRef.current);
    const signature = graph.nodes.map((node) => `${node.id}:${node.state}`).join("|");
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });
    if (signature !== signatureRef.current) {
      signatureRef.current = signature;
      cy.layout({
        name: "cose",
        animate: false,
        padding: 24,
        nodeRepulsion: () => 6400,
        idealEdgeLength: () => 60,
      }).run();
    }
    cy.resize();
  }, [graph, version]);

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
    <section className="flex min-h-0 flex-col rounded-xl border border-zinc-800 bg-zinc-900/60">
      <header className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
        <h2 className="text-sm font-semibold text-zinc-200">Knowledge graph</h2>
        <span className="text-[11px] text-zinc-500">What the AI thinks it knows</span>
      </header>
      <div className="relative min-h-[260px] flex-1">
        <div ref={containerRef} className="absolute inset-0" aria-label="Workspace knowledge graph" />
        {graph && graph.nodes.length === 0 ? (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-zinc-500">
            Waiting for the agent to touch the workspace.
          </p>
        ) : null}
      </div>
      <footer className="border-t border-zinc-800 px-3 py-2">
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {states.map((state) => {
            const meta = NODE_STATE_META[state];
            return (
              <li key={state} className="flex items-center gap-1.5 text-[11px] text-zinc-400">
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
        <p className="mt-1 text-[11px] text-zinc-500">
          Green: the agent has seen it. Grey: it has not. Double-click a folder to collapse it.
        </p>
      </footer>
    </section>
  );
}
