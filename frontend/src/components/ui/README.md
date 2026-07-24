# InvoiceIQ UI kit

The reusable, accessible primitives every page composes from. One import surface,
one set of conventions — so a table, a button, or an empty state looks and behaves
the same everywhere, and a new page is assembled instead of hand-rolled.

```ts
import { Button, Card, DataTable, EmptyState, Badge, type Column } from "../components/ui";
```

## Why this exists

Before the kit the frontend hand-rolled a `<table>` on 17 pages, an inline `.card`
24 times, and ad-hoc loading/empty states a dozen times each — every one slightly
different, none with skeletons or empty states, accessibility applied unevenly. The
kit consolidates those into eight primitives with the async states, keyboard
support, and responsive behavior built in once.

## Architecture

```
components/
  ui/
    index.ts        ← the single public surface (barrel). Import from here, never deep-path.
    Button.tsx      ← the one button primitive (variants, loading, icons)
    Badge.tsx       ← status pill + Dot; a fixed semantic Tone palette
    Card.tsx        ← surface container (+ StatCard metric tile)
    DataTable.tsx   ← column-driven table: skeletons, empty state, a11y, responsive
    EmptyState.tsx  ← the canonical "nothing here yet" surface
    QueryState.tsx  ← wraps a TanStack query → loading / error / empty / data
    Spinner.tsx     ← accessible spinner (role=status)
    Skeleton.tsx    ← content-shaped placeholders (+ SkeletonText)
    README.md       ← this file
```

Design principles:

- **One public surface.** Everything is re-exported from `index.ts`. Pages import
  from `"../components/ui"` and never reach into a file directly — internals can be
  refactored without touching callers.
- **Composition over configuration.** `DataTable` takes a `columns` array whose
  `cell` renders arbitrary JSX (a `Badge`, a `Switch`, a `Button`) rather than a
  prop for every possible variation. Small primitives compose into rich views.
- **Accessibility is not opt-in.** Focus-visible rings, `aria-busy`, `role=status`,
  scoped `<th>`, sr-only captions, and keyboard-activatable rows are baked into the
  primitives, so every page inherits them for free.
- **Semantic, not literal, styling.** Callers pass intent (`tone="danger"`,
  `variant="primary"`) — never raw Tailwind color classes. The palette lives in one
  place and can be re-themed centrally.
- **Structural typing at the seams.** `QueryState` accepts a minimal `QueryLike`
  shape rather than importing TanStack's generics, so it stays decoupled.

## Components & props

### `Button`
`forwardRef` button — the only button primitive.

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `variant` | `primary \| secondary \| ghost \| danger \| subtle` | `primary` | Semantic intent, not color |
| `size` | `sm \| md` | `md` | |
| `loading` | `boolean` | `false` | Shows a spinner, sets `aria-busy`, disables clicks (no double-submit) |
| `leftIcon` | `ReactNode` | — | Hidden while loading |
| `fullWidth` | `boolean` | `false` | |
| …`ButtonHTMLAttributes` | | | `onClick`, `disabled`, `type` (defaults to `"button"`), etc. |

```tsx
<Button loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
<Button variant="secondary" size="sm" disabled={page <= 1}>Previous</Button>
```

### `Badge` / `Dot`
Status pill and a paired colored dot.

| Prop | Type | Default |
| --- | --- | --- |
| `tone` | `neutral \| brand \| success \| warning \| danger \| info` | `neutral` |

```tsx
<Badge tone="success">active</Badge>
<Badge tone={m.is_active ? "success" : "neutral"}>{m.is_active ? "active" : "disabled"}</Badge>
```

### `Card` / `StatCard`
`Card` is the surface container; `title`/`actions` render an optional header row,
`padded={false}` for edge-to-edge content (e.g. a bare table). `StatCard` is a
compact metric tile (`label` · big `value` · `sub`, with an `accent`).

```tsx
<Card title="Chain integrity" actions={<Button size="sm">Verify</Button>}>
  <p className="text-xs text-slate-400">…</p>
</Card>

<StatCard label="Outstanding" value="€12,480" sub="7 invoices" accent="rose" />
```

### `DataTable<T>`
The flagship. Column-driven, with loading skeletons, an empty state, horizontal
scroll on small screens, and accessible markup.

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `columns` | `Column<T>[]` | — | `{ key, header, cell(row, i), align?, className?, width? }` |
| `rows` | `T[] \| undefined` | — | `undefined` reads as "not loaded yet" |
| `rowKey` | `(row: T) => string` | — | Stable React key |
| `loading` | `boolean` | `false` | Renders `skeletonRows` shimmer rows |
| `skeletonRows` | `number` | `6` | |
| `empty` | `ReactNode` | `<EmptyState/>` | Shown at zero rows |
| `caption` | `string` | — | sr-only table caption |
| `onRowClick` | `(row: T) => void` | — | Makes rows keyboard-activatable (Enter/Space) |
| `rowClassName` | `(row: T) => string` | — | Per-row styling |
| `dense` | `boolean` | `false` | Tighter padding |

```tsx
const columns: Column<Member>[] = [
  { key: "member", header: "Member", cell: (m) => <strong>{m.name}</strong> },
  { key: "status", header: "Status", cell: (m) => <Badge tone={m.is_active ? "success" : "neutral"}>…</Badge> },
];

<DataTable
  caption="Workspace members"
  columns={columns}
  rows={members.data}
  rowKey={(m) => m.id}
  loading={members.isLoading}
  empty={<EmptyState title="No members yet" description="Invite teammates to collaborate." />}
/>
```

### `EmptyState`
The canonical "there's nothing here (yet)" surface — used inside tables, lists,
and cards so empty states look intentional.

```tsx
<EmptyState title="No events yet" description="Actions will appear here as they happen." action={<Button>Refresh</Button>} />
```

### `QueryState<T>`
Standardizes the async triad (loading / error / empty) around a TanStack query so
data views stop reinventing `isLoading &&` checks. Renders an error `EmptyState`
with a Retry button, your `loading` node, your `empty` node, or `children(data)`.

```tsx
<QueryState query={q} isEmpty={(d) => d.items.length === 0}
            loading={<SkeletonText lines={4} />}
            empty={<EmptyState title="No invoices" />}>
  {(data) => <DataTable rows={data.items} … />}
</QueryState>
```

### `Spinner` / `Skeleton` / `SkeletonText`
Low-level loading primitives. `Spinner` announces itself via `role=status` +
sr-only label. `Skeleton`/`SkeletonText` are content-shaped placeholders that
respect `prefers-reduced-motion` (pulse disabled under `motion-reduce`).

## Best practices

- **Import from the barrel** (`../components/ui`), never a deep path.
- **Pass intent, not classes.** `tone="danger"`, `variant="primary"` — keep raw
  color utilities out of pages so the palette stays central.
- **Let `DataTable` own the async states.** Pass `rows={query.data}` +
  `loading={query.isLoading}` and provide an `empty` — don't gate the whole table
  behind `isLoading &&`, or you lose the skeletons.
- **Always give a table a `caption` and every non-text control an accessible name**
  (e.g. `<Switch label="…">`, `aria-label` on a bare `<select>` in a cell).
- **Reuse `Button`'s `loading`** for any async action — it blocks double-submits
  and sets `aria-busy` for you.
- **Reach for `StatCard` for metric tiles** instead of re-styling a `.card`.

## Reference implementations

Two pages are built entirely on the kit — copy their patterns:

- `src/pages/Audit.tsx` — `DataTable` with `Badge`-toned cells, `Card` header with a
  loading `Button`, filter + pagination, `EmptyState`.
- `src/pages/Team.tsx` — `DataTable` with interactive cells (`Switch`, `<select>`,
  `Button`), a `Card`-wrapped invite form, per-row actions.
