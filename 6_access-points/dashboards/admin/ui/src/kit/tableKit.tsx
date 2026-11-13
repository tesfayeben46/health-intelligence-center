/**
 * tableKit: the admin SPA's local table helper. Every large table gets a fixed
 * height and pagination, plus search / sort / filter controls where the data
 * warrants them.
 *
 * A hook plus small parts instead of a monolithic <Table>:
 * admin tables vary wildly (expandable fleet rows, avatar cells, pill
 * columns), so each page keeps its own <table> markup and composes:
 *
 *   const kit = useTableKit(rows, { pageSize, search, sortVals, initialSort });
 *   <TableToolbar kit={kit} placeholder="Search…">{custom filter selects}</TableToolbar>
 *   <div className="tk-scroll"><table className="dash">
 *     <thead><tr><SortTh kit={kit} k="name">Name</SortTh>…</tr></thead>
 *     <tbody>{kit.pageRows.map(…)}</tbody>
 *   </table></div>
 *   <TablePager kit={kit} />
 *
 * Dashboards do not share modules with each other; within the admin SPA this
 * helper is the one shared piece.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import '../styles/tablekit.css';

export type SortDir = 'asc' | 'desc';
export type SortVal = string | number | null | undefined;

export type TableKitOpts<T> = {
  /** Rows per page (default 10). */
  pageSize?: number;
  /** Haystack builder; providing it enables the toolbar search box. */
  search?: (row: T) => string;
  /** Sortable columns: key to value extractor. */
  sortVals?: Record<string, (row: T) => SortVal>;
  initialSort?: { key: string; dir: SortDir };
};

export type TableKit<T> = {
  pageRows: T[];
  /** All rows surviving search, before pagination (for exports and counts). */
  filteredRows: T[];
  total: number;           // rows in the source
  filteredTotal: number;   // rows after search
  page: number;            // 1-based, clamped
  pages: number;
  setPage: (p: number) => void;
  pageSize: number;
  query: string;
  setQuery: (q: string) => void;
  searchable: boolean;
  sort: { key: string; dir: SortDir } | null;
  toggleSort: (key: string) => void;
  sortable: (key: string) => boolean;
};

function cmp(a: SortVal, b: SortVal): number {
  const an = a == null, bn = b == null;
  if (an && bn) return 0;
  if (an) return 1;                       // nulls sort last in ascending order
  if (bn) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

export function useTableKit<T>(rows: T[] | null | undefined, opts: TableKitOpts<T> = {}): TableKit<T> {
  const pageSize = opts.pageSize ?? 10;
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(opts.initialSort ?? null);

  const source = rows ?? [];

  const filteredRows = useMemo(() => {
    let out = source;
    const q = query.trim().toLowerCase();
    if (q && opts.search) {
      out = out.filter(r => opts.search!(r).toLowerCase().includes(q));
    }
    if (sort && opts.sortVals?.[sort.key]) {
      const get = opts.sortVals[sort.key];
      out = [...out].sort((a, b) => (sort.dir === 'asc' ? 1 : -1) * cmp(get(a), get(b)));
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, query, sort]);

  const pages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
  const safePage = Math.min(page, pages);
  useEffect(() => { setPage(1); }, [query]);           // new search: back to the first page

  const pageRows = useMemo(
    () => filteredRows.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filteredRows, safePage, pageSize]);

  return {
    pageRows,
    filteredRows,
    total: source.length,
    filteredTotal: filteredRows.length,
    page: safePage,
    pages,
    setPage: (p: number) => setPage(Math.max(1, Math.min(pages, p))),
    pageSize,
    query,
    setQuery,
    searchable: !!opts.search,
    sort,
    toggleSort: (key: string) => {
      if (!opts.sortVals?.[key]) return;
      setSort(s => (s?.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'asc' }));
      setPage(1);
    },
    sortable: (key: string) => !!opts.sortVals?.[key],
  };
}

/* toolbar: search box + custom filter controls + live count */

export function TableToolbar<T>({ kit, placeholder, children, unit = 'rows' }: {
  kit: TableKit<T>;
  placeholder?: string;
  children?: ReactNode;      // extra filter controls (selects, toggles…)
  unit?: string;
}) {
  return (
    <div className="tk-toolbar">
      {kit.searchable && (
        <span className="search-row tk-search">
          <span className="search-ico"><svg className="ico ico-sm"><use href="#i-search" /></svg></span>
          <input
            className="search-input"
            placeholder={placeholder ?? 'Search…'}
            value={kit.query}
            onChange={e => kit.setQuery(e.target.value)}
          />
        </span>
      )}
      {children}
      <span className="tk-spacer" />
      <span className="tk-count muted">
        {kit.filteredTotal === kit.total
          ? `${kit.total} ${unit}`
          : `${kit.filteredTotal} of ${kit.total} ${unit}`}
      </span>
    </div>
  );
}

/* sortable header cell */

export function SortTh<T>({ kit, k, children, className }: {
  kit: TableKit<T>;
  k: string;
  children: ReactNode;
  className?: string;
}) {
  const active = kit.sort?.key === k;
  const canSort = kit.sortable(k);
  return (
    <th
      className={`${className ?? ''} ${canSort ? 'tk-sortable' : ''} ${active ? 'tk-sorted' : ''}`.trim() || undefined}
      onClick={canSort ? () => kit.toggleSort(k) : undefined}
      aria-sort={active ? (kit.sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
    >
      <span className="tk-th-inner">
        {children}
        {canSort && (
          <span className="tk-arrow" aria-hidden>
            {active ? (kit.sort!.dir === 'asc' ? '▲' : '▼') : '↕'}
          </span>
        )}
      </span>
    </th>
  );
}

/* pager footer */

export function TablePager<T>({ kit }: { kit: TableKit<T> }) {
  if (kit.pages <= 1) return null;
  const from = (kit.page - 1) * kit.pageSize + 1;
  const to = Math.min(kit.filteredTotal, kit.page * kit.pageSize);
  return (
    <div className="tk-pager">
      <span className="muted">{from} to {to} of {kit.filteredTotal}</span>
      <span className="tk-spacer" />
      <button className="btn btn-sm btn-ghost" disabled={kit.page <= 1} onClick={() => kit.setPage(kit.page - 1)}>
        ‹ Prev
      </button>
      <span className="tk-pageno muted">page {kit.page} / {kit.pages}</span>
      <button className="btn btn-sm btn-ghost" disabled={kit.page >= kit.pages} onClick={() => kit.setPage(kit.page + 1)}>
        Next ›
      </button>
    </div>
  );
}
