# App-shell runtime: master-detail, modals and entity data

The widget runtime renders a grid of cards over governed mart data
(`renderPage` to sections to `WidgetDef.render`). Some surfaces, such as the
workshop dashboard, need a different shape: entity CRUD with master-detail
navigation and modals over the ops REST routes.

The app shell adds that shape without changing the grid path. A plain grid page
(`CatalogPage` to `renderPage`) renders as before; the app shell is opt-in via
`renderAppShell`. Everything lives in `runtime/*` plus the `details/` and
`modals/` folders, so the same runtime is copied into `workshop/ui`.

## The three contracts (in `runtime/types.ts`)

### 1. Entity data: `ctx.fetchEntity(path, params)`

For non-mart data. Mart widgets use `ctx.fetchData()` (the OPA `widget_data`
path, which returns empty stubs for anything that is not a governed mart).
Entity data comes from ops REST routes that are authorization-gated at their
own gateway route (`/gw/projects`, `/gw/artifacts`, `/gw/ml`, and so on):

```ts
const rows = await ctx.fetchEntity('/gw/projects');   // rows or null
if (!rows) { ctx.empty('No projects for your role.'); return; }
```

The invariant matches `fetchData`: real rows or `null` (empty, denied or error),
and `null` means drawing an empty state. Results are
cached per session by path and params (`dataHook.ts`); call
`invalidateEntityData()` after a create, update or delete. Available on
`WidgetCtx`, `DetailCtx` and `ModalCtx`.

### 2. Master-detail: `host.openDetail(kind, id, props?)` / `host.closeDetail()`

A widget navigates to a detail view for a selected entity; the shell swaps the
page for the registered `DetailDef` whose `kind` matches, and `closeDetail`
returns to the page. `host.currentDetail` is the active route (or `null`).

```ts
// in a list widget:
ctx.host.openDetail('project', row.id, { row });   // pass the row for an instant first paint
```

A `DetailDef` lives in `src/details/<kind>/detail.ts`:

```ts
import type { DetailDef, DetailCtx } from '../../runtime/types';
const def: DetailDef = {
  kind: 'project',
  async render(ctx: DetailCtx) {
    const [p] = (await ctx.fetchEntity(`/gw/projects/${ctx.route.id}`)) || [];
    if (!p) { ctx.empty('Project not found.'); return; }
    // …draw ctx.el… ; a "Back" button calls ctx.host.closeDetail()
  },
};
export default def;
```

`detailRegistry.ts` discovers detail views at build time (an eager glob, like
the widget registry).

### 3. Modal host: `host.openModal(id, props?)` / `host.closeModal()`

A widget or detail view opens a registered `ModalDef` in the shell's single
modal slot; the shell draws the backdrop, frame, title and close button, and
the `ModalDef` fills the body. `host.currentModal` is the open request (or
`null`).

```ts
ctx.host.openModal('project-create', { onDone: () => refresh() });
```

A `ModalDef` lives in `src/modals/<id>/modal.ts`:

```ts
import type { ModalDef, ModalCtx } from '../../runtime/types';
const def: ModalDef = {
  id: 'project-create',
  title: 'New project',
  render(ctx: ModalCtx) {
    // …draw a form into ctx.el…
    // on submit: POST via authFetch, then invalidateEntityData():
    //            ctx.props.onDone?.(), then ctx.close()
  },
};
export default def;
```

`modalRegistry.ts` discovers modals at build time. The caller owns the refresh
(it passes `onDone` in props), so the modal stays decoupled from whatever opened
it. `props.title` overrides the title for one opening.

## Authoring a master-detail page with modals

1. List widget (`src/widgets/<id>/widget.ts`): `ctx.fetchEntity(...)` for the
   rows; each row's action calls `ctx.host.openDetail(kind, id, { row })`; a
   toolbar button calls `ctx.host.openModal('<create>', { onDone })`.
2. Detail view (`src/details/<kind>/detail.ts`): reads the entity via
   `ctx.fetchEntity`, calls `ctx.host.closeDetail()` to go back, and may open
   modals.
3. Modals (`src/modals/<id>/modal.ts`): the create, submit or compute flow;
   POST via `authFetch`, then `invalidateEntityData()`, `props.onDone?.()` and
   `ctx.close()`.
4. Mount with `renderAppShell(page, mount)` instead of `renderPage`. It composes
   the grid, the detail swap and the modal slot, all driven by the host shell
   store (`subscribeShell`).

With no route or modal active, `renderAppShell` renders the page exactly like
`renderPage`, so a page can adopt the app shell and keep its grid widgets.

## Dev harness

A reference harness sits behind the hash `#appshell-demo` (inert on every
live page; `App.tsx` gates it):

- `src/sections/DemoAppShell.tsx`: builds a synthetic page with one section and
  mounts it through `renderAppShell`
- `src/details/demo-mart/detail.ts`: the detail view (kind `demo-mart`)
- `src/modals/demo-info/modal.ts`: the modal (id `demo-info`)

The synthetic page places a `demo-appshell-list` widget, which has no module
under `src/widgets/`, so the harness currently renders the registry's "no
renderer" placeholder in place of the list.
