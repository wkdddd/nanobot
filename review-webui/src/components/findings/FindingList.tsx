import { useState, useMemo, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Search, Inbox, Layers, FolderTree, ChevronDown, ChevronRight } from "lucide-react";
import { FindingCard } from "./FindingCard";
import type { Finding } from "@/hooks/useReviewSession";

interface FindingListProps {
  findings: Finding[];
  activeDimension: string | null;
  onSelectFinding?: (finding: Finding) => void;
}

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const SEVERITY_GROUP_LABELS: Record<string, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

const SEVERITY_GROUP_COLORS: Record<string, string> = {
  critical: "text-red-600",
  high: "text-orange-600",
  medium: "text-yellow-600",
  low: "text-blue-600",
};

type GroupMode = "severity" | "file";

const PAGE_SIZE = 10;

export function FindingList({ findings, activeDimension, onSelectFinding }: FindingListProps) {
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [dimensionFilter, setDimensionFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [groupMode, setGroupMode] = useState<GroupMode>("severity");
  const [compact, setCompact] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [expandedPages, setExpandedPages] = useState<Set<string>>(new Set());

  const uniqueDimensions = useMemo(() => {
    const dims = new Set(findings.map((f) => f.dimension));
    return Array.from(dims).sort();
  }, [findings]);

  const filteredFindings = useMemo(() => {
    let result = [...findings];
    if (activeDimension) {
      result = result.filter((f) => f.dimension === activeDimension);
      setDimensionFilter(activeDimension);
    }
    if (severityFilter !== "all") {
      result = result.filter((f) => f.severity.toLowerCase() === severityFilter);
    }
    if (dimensionFilter !== "all") {
      result = result.filter((f) => f.dimension === dimensionFilter);
    }
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (f) =>
          f.title.toLowerCase().includes(query) ||
          f.file.toLowerCase().includes(query) ||
          f.impact?.toLowerCase().includes(query) ||
          f.recommendation?.toLowerCase().includes(query),
      );
    }
    result.sort(
      (a, b) =>
        (SEVERITY_ORDER[a.severity.toLowerCase()] ?? 99) -
        (SEVERITY_ORDER[b.severity.toLowerCase()] ?? 99),
    );
    return result;
  }, [findings, activeDimension, severityFilter, dimensionFilter, searchQuery]);

  // Group findings
  const grouped = useMemo(() => {
    const groups: Record<string, Finding[]> = {};
    for (const f of filteredFindings) {
      const key = groupMode === "severity" ? f.severity.toLowerCase() : f.file;
      if (!groups[key]) groups[key] = [];
      groups[key].push(f);
    }
    return Object.entries(groups).sort(([a], [b]) => {
      if (groupMode === "severity") {
        return (SEVERITY_ORDER[a] ?? 99) - (SEVERITY_ORDER[b] ?? 99);
      }
      return a.localeCompare(b);
    });
  }, [filteredFindings, groupMode]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const togglePage = useCallback((key: string) => {
    setExpandedPages((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const getFindingId = (f: Finding, index: number) =>
    `${f.file}:${f.line ?? 0}:${index}`;

  const visibleCount = filteredFindings.length;

  return (
    <div className="space-y-2">
      {/* Filter bar */}
      <div className="flex flex-col sm:flex-row gap-1.5">
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <Input
            placeholder="Search findings..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-8 h-8 text-xs"
            name="search"
          />
        </div>

        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
        >
          <option value="all">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        <select
          value={activeDimension ?? dimensionFilter}
          onChange={(e) => setDimensionFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
        >
          <option value="all">All Dimensions</option>
          {uniqueDimensions.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
      </div>

      {/* Toolbar: group mode, compact toggle, count */}
      <div className="flex items-center justify-between gap-1.5">
        <div className="flex items-center gap-1">
          {/* Group mode toggle */}
          <button
            type="button"
            onClick={() => setGroupMode(groupMode === "severity" ? "file" : "severity")}
            className={cn(
              "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
              "border-border bg-background hover:bg-accent/50"
            )}
            aria-label={`Group by ${groupMode === "severity" ? "file" : "severity"}`}
          >
            {groupMode === "severity" ? (
              <Layers className="w-3 h-3 text-muted-foreground" />
            ) : (
              <FolderTree className="w-3 h-3 text-muted-foreground" />
            )}
            <span>By {groupMode === "severity" ? "Severity" : "File"}</span>
          </button>

          {/* Compact toggle */}
          <button
            type="button"
            onClick={() => setCompact(!compact)}
            className={cn(
              "inline-flex items-center rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
              compact
                ? "border-primary/30 bg-primary/5 text-primary"
                : "border-border bg-background hover:bg-accent/50 text-muted-foreground"
            )}
            aria-label={compact ? "Expand view" : "Compact view"}
          >
            {compact ? "Detailed" : "Compact"}
          </button>
        </div>

        <span className="text-[11px] text-muted-foreground">
          Showing {visibleCount} of {findings.length} findings
        </span>
      </div>

      {/* Grouped findings */}
      {grouped.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Inbox className="w-10 h-10 mb-3 opacity-40" />
          <p className="text-sm font-medium">No findings</p>
          <p className="text-xs mt-1">
            {findings.length === 0
              ? "Review has not produced any findings yet."
              : "No findings match the current filters."}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {grouped.map(([groupKey, items]) => {
            const isCollapsed = collapsedGroups.has(groupKey);
            const needsPaging = items.length > PAGE_SIZE;
            const isExpanded = !needsPaging || expandedPages.has(groupKey);
            const visibleItems = isExpanded ? items : items.slice(0, PAGE_SIZE);
            const hiddenCount = items.length - PAGE_SIZE;

            return (
              <div key={groupKey}>
                {/* Group header - collapsible */}
                <button
                  type="button"
                  onClick={() => toggleGroup(groupKey)}
                  className="flex items-center gap-2 w-full text-left mb-1.5 group"
                >
                  {isCollapsed ? (
                    <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                  )}
                  <span
                    className={cn(
                      "text-xs font-semibold uppercase tracking-wider",
                      groupMode === "severity"
                        ? SEVERITY_GROUP_COLORS[groupKey] || "text-muted-foreground"
                        : "text-foreground"
                    )}
                  >
                    {groupMode === "severity"
                      ? (SEVERITY_GROUP_LABELS[groupKey] ?? groupKey)
                      : groupKey}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    ({items.length})
                  </span>
                </button>

                {/* Items */}
                {!isCollapsed && (
                  <div className="space-y-1 ml-1">
                    {visibleItems.map((finding, idx) => {
                      const id = getFindingId(finding, idx);
                      return (
                        <FindingCard
                          key={id}
                          finding={finding}
                          expanded={expandedId === id}
                          compact={compact}
                          onToggle={() => {
                            setExpandedId(expandedId === id ? null : id);
                            onSelectFinding?.(finding);
                          }}
                        />
                      );
                    })}

                    {/* Show more button */}
                    {needsPaging && !isExpanded && (
                      <button
                        type="button"
                        onClick={() => togglePage(groupKey)}
                        className="w-full py-1.5 text-[11px] text-primary font-medium hover:bg-primary/5 rounded-md transition-colors"
                      >
                        Show {hiddenCount} more...
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
