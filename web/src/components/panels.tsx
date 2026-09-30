import { type ReactNode, type RefObject } from "react";
import { Database, Layers3, Map, Network, RefreshCcw, Route, Search, Settings2, Waypoints } from "lucide-react";
import type { AtlasFilters } from "../graph";
import type { AtlasPayload } from "../types";
import type { NavSection } from "../ui";
import { StatRow, SwitchRow, ToggleRow } from "./primitives";

export function NavRail({
  activeSection,
  onSelect
}: {
  activeSection: NavSection;
  onSelect: (section: NavSection) => void;
}) {
  return (
    <aside className="nav-rail">
      <div className="brand-mark">
        <Network size={25} />
      </div>
      <RailButton icon={<Map size={19} />} label="Map" active={activeSection === "map"} onClick={() => onSelect("map")} />
      <RailButton icon={<Layers3 size={19} />} label="Atlas" active={activeSection === "atlas"} onClick={() => onSelect("atlas")} />
      <RailButton icon={<Route size={19} />} label="Routes" active={activeSection === "routes"} onClick={() => onSelect("routes")} />
      <RailButton icon={<Database size={19} />} label="Catalog" active={activeSection === "catalog"} onClick={() => onSelect("catalog")} />
      <div className="rail-spacer" />
      <RailButton icon={<Settings2 size={19} />} label="Settings" disabled />
      <div className="local-first">
        <span />
        Local-first
      </div>
    </aside>
  );
}

function RailButton({
  icon,
  label,
  active = false,
  disabled = false,
  onClick
}: {
  icon: ReactNode;
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      className={`rail-button ${active ? "active" : ""}`}
      disabled={disabled}
      onClick={onClick}
      title={disabled ? `${label} unavailable in this view` : label}
      type="button"
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

export function LeftPanel({
  atlas,
  filters,
  onFiltersChange,
  panelRef,
  searchInputRef
}: {
  atlas: AtlasPayload;
  filters: AtlasFilters;
  onFiltersChange: (filters: AtlasFilters) => void;
  panelRef: RefObject<HTMLElement | null>;
  searchInputRef: RefObject<HTMLInputElement | null>;
}) {
  const allDomainsSelected = filters.domains.length === atlas.domains.length;
  return (
    <aside className="left-panel" ref={panelRef}>
      <div className="panel-section title-section">
        <h1>SkillRoute</h1>
        <a className="brand-credit" href="https://jestats.io" target="_blank" rel="noreferrer">by JEStats</a>
      </div>
      <div className="panel-section">
        <div className="section-label">Atlas Controls</div>
        <label className="search-box">
          <Search size={15} />
          <input
            ref={searchInputRef}
            value={filters.search}
            onChange={(event) => onFiltersChange({ ...filters, search: event.target.value })}
            placeholder="Search skills, facets, tags..."
          />
          <kbd>/</kbd>
        </label>
      </div>
      <div className="panel-section">
        <div className="section-label">Graph Mode</div>
        <div className="segmented">
          <button className="active" type="button">Facet Nebula</button>
          <button disabled title="Skill Graph is not available in this view" type="button">Skill Graph</button>
          <button disabled title="Matrix is not available in this view" type="button">Matrix</button>
        </div>
      </div>
      <div className="panel-section grow">
        <div className="section-heading">
          <span>Facets / Domains</span>
          <button
            type="button"
            onClick={() =>
              onFiltersChange({
                ...filters,
                domains: allDomainsSelected ? [atlas.domains[0]?.id ?? "uncategorized"] : atlas.domains.map((domain) => domain.id)
              })
            }
          >
            {allDomainsSelected ? "Focus" : "Select all"}
          </button>
        </div>
        <div className="domain-list">
          {atlas.domains.map((domain) => (
            <ToggleRow
              key={domain.id}
              color={domain.color}
              label={domain.name}
              value={domain.count}
              checked={filters.domains.includes(domain.id)}
              onChange={() => {
                const next = filters.domains.includes(domain.id)
                  ? filters.domains.filter((item) => item !== domain.id)
                  : [...filters.domains, domain.id];
                onFiltersChange({ ...filters, domains: next.length ? next : [domain.id] });
              }}
            />
          ))}
        </div>
      </div>
      <div className="panel-section">
        <div className="section-label">Relationship Types</div>
        <div className="relationship-toggle-list">
          {atlas.relationshipTypes.map((relationship) => (
            <ToggleRow
              key={relationship.type}
              color={relationship.color}
              label={relationship.type.replace("_", " ")}
              value={relationship.count}
              checked={filters.relationshipTypes.includes(relationship.type)}
              onChange={() => {
                const next = filters.relationshipTypes.includes(relationship.type)
                  ? filters.relationshipTypes.filter((item) => item !== relationship.type)
                  : [...filters.relationshipTypes, relationship.type];
                onFiltersChange({ ...filters, relationshipTypes: next.length ? next : [relationship.type] });
              }}
            />
          ))}
        </div>
      </div>
      <div className="panel-section">
        <div className="section-label">Filters</div>
        <SwitchRow
          label="Orphans only"
          value={atlas.catalog.orphanCount}
          checked={filters.orphansOnly}
          onChange={() => onFiltersChange({ ...filters, orphansOnly: !filters.orphansOnly })}
        />
        <SwitchRow
          label="Conflicts only"
          value={atlas.catalog.conflictCount}
          checked={filters.conflictsOnly}
          onChange={() => onFiltersChange({ ...filters, conflictsOnly: !filters.conflictsOnly })}
        />
      </div>
      <CatalogStats atlas={atlas} />
    </aside>
  );
}

function CatalogStats({ atlas }: { atlas: AtlasPayload }) {
  return (
    <div className="catalog-card">
      <div className="section-label">Catalog Stats</div>
      <StatRow label="Total Skills" value={atlas.catalog.skillCount} />
      <StatRow label="Facets" value={atlas.catalog.domainCount} />
      <StatRow label="Relationships" value={atlas.catalog.relationshipCount} />
      <StatRow label="Orphans" value={atlas.catalog.orphanCount} color="#fb923c" />
      <StatRow label="Conflicts" value={atlas.catalog.conflictCount} color="#f87171" />
      <StatRow label="Unresolved" value={atlas.catalog.unresolvedRelationshipCount} />
      <div className="stats-footer">Backend {Object.keys(atlas.catalog.backendRefCounts)[0] ?? "local-token:indexed"}</div>
    </div>
  );
}

export function TopBar({
  atlas,
  onRefresh,
  onRelayout,
  refreshing
}: {
  atlas: AtlasPayload;
  onRefresh: () => void;
  onRelayout: () => void;
  refreshing: boolean;
}) {
  return (
    <header className="top-bar">
      <div>
        <div className="top-title">Skill Universe</div>
        <div className="top-meta">
          {atlas.catalog.skillCount} skills • {atlas.catalog.relationshipCount} relationships • {atlas.catalog.path}
        </div>
      </div>
      <div className="top-actions">
        <span className="layout-chip">Layout: Radial</span>
        <button aria-label="Relayout" onClick={onRelayout} type="button"><Waypoints size={17} /></button>
        <button
          aria-label="Refresh"
          className={refreshing ? "loading" : ""}
          disabled={refreshing}
          onClick={onRefresh}
          type="button"
        >
          <RefreshCcw size={17} />
        </button>
      </div>
    </header>
  );
}

export function RelationshipLegend({ atlas }: { atlas: AtlasPayload }) {
  return (
    <div className="legend">
      <div className="section-label">Legend</div>
      {atlas.relationshipTypes.map((relationship) => (
        <div key={relationship.type} className="legend-row">
          <span style={{ background: relationship.color }} />
          {relationship.type.replace("_", " ")}
        </div>
      ))}
      <div className="legend-row selected"><span /> selected path</div>
    </div>
  );
}
