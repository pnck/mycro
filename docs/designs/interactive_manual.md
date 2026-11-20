# Interactive Manual & Token Inspector: Interaction Contract

## 1. Purpose and Scope

The Interactive Manual is a non-modal documentation drawer for the MYCRO Web IDE. It combines a searchable DSL catalog, configurable snippet insertion, and cursor-aware completion/diagnostics while leaving the editor usable.

This document describes current UI behavior. `spec/dsl.md`, `spec/runtime.md`, and `spec/vm.md` remain normative for the language. The frontend catalog and completion data live in `web/src/modules/catalog.ts`; drawer, inspector, and editor behavior live in `web/src/modules/manual/`, `token-detector.ts`, `token-inspector.ts`, and `editor.ts`.

## 2. Drawer Layout and Lifecycle

### 2.1 Layout

- The drawer is a fixed right-side overlay: `440px` wide, at most `90vw`, full viewport height, `z-index: 1000`.
- It overlays rather than resizes the editor. There is no backdrop or modal focus trap.
- The header contains the title, close button, search, and Expand All / Collapse All actions.
- The Live Inspector remains between the header and the independently scrolling catalog.
- The catalog and candidate tray contain overscroll. The tray wraps chips and scrolls after reaching 120px high.
- The floating Manual tab sits at the lower-right edge, behind the open drawer.

### 2.2 Opening and closing

Opening through either the toolbar **Manual** button or the floating tab:

1. Slides the drawer in.
2. Focuses the search input, so immediate typing filters the catalog rather than editing code.
3. Saves the open state.

Only the header `✕` button closes the drawer. Outside clicks and `Escape` have no manual dismissal binding. Clicking, selecting, and typing in the editor remain available while it is open.

### 2.3 Persistence

| State | Across reloads |
|---|---|
| Drawer open/closed | Persisted |
| Categories collapsed through headers or bulk actions | Persisted by category name |
| Search query and assembly field values | Not persisted |
| Catalog or candidate-tray scroll offsets | Not persisted |
| Expansion caused by search or cross-reference navigation | DOM-only; not added to the saved collapsed list |

An initially open drawer is restored without an opening animation. Storage/JSON failures fall back silently to in-memory defaults.

## 3. Catalog Navigation and Search

### 3.1 Sections

`MANUAL_DATA` currently renders 17 ordered DSL categories. A section header shows its title, a **category/type badge** (not an item count), and a chevron.

Headers use native `position: sticky; top: 0` inside the catalog. Clicking one toggles the section and persists that explicit choice. **Expand All** and **Collapse All** apply to every rendered section, including sections currently hidden by search.

Descriptions in the exact form `(See 'Category Name'.)` become links. Activating one expands the exact-name target, smoothly scrolls it into view, and highlights it for 1200ms. This temporary expansion does not change the persisted collapsed list.

### 3.2 Search

Search is a trimmed, case-insensitive substring filter over item name, syntax, description, snippet, aliases, HID usage ID, return text, and category name/description. Notes, table cells, and separate field metadata are not independently indexed.

- Nonmatching items and sections with no matches are hidden.
- Matching sections expand temporarily.
- A zero-result message appears when appropriate.
- Clearing shows all content and refocuses search, but does **not** restore the expansion state from before filtering; sections expanded by search remain open.

## 4. Cards, Assembly, and Insertion

### 4.1 Card types and controls

- Ordinary insertable cards show a `+` action.
- Recipe cards show multiline examples and a `+` action.
- Reference cards with notes or tables show a `Norm` badge and no insert action.
- Optional metadata shows aliases, HID usage IDs, and return descriptions.
- Parameterized items offer text or select controls with defaults. Values are substituted when inserting; they are not DSL-validated.
- Items with aliases and no explicit fields offer a **Variant** selector containing the canonical command and aliases.

Within the catalog, only `+` buttons insert content. Command labels and code blocks are not clickable insertion surfaces.

### 4.2 Insert behavior

Clicking `+`, or pressing `Enter` in an assembly **text input**, inserts at the editor cursor or replaces its selection. Enter on a select does not trigger this path.

Insertion focuses the editor, updates syntax highlighting/workspace state and the inspector, and creates an immediate entry in the project's custom undo history. It never touches the system clipboard and leaves the drawer open. The button becomes a green checkmark for **600ms**.

The editor history holds at most 100 text/selection snapshots. Ordinary typing coalesces for 300ms; discrete edits are recorded immediately. `Ctrl/Cmd+Z` undoes, while `Ctrl/Cmd+Y` and `Ctrl/Cmd+Shift+Z` redo. These bindings use the custom history manager, not native textarea undo.

## 5. Live Token Inspector

### 5.1 Update model and scope

The inspector updates on editor input, keyup, click, focus, and active-editor selection changes, even while the drawer is closed. One lexical scan feeds scope, structure status, and candidates.

Scope breadcrumbs describe nested command/block positions, argument roles, layout/comment/verbatim lines, and transitions awaiting a required block. This is an advisory lexical model, not an AST or proof that the macro compiles.

### 5.2 Structural status

The status badge reports, in priority order: scanner syntax errors, unclosed verbatim blocks, tracked unmatched closers, incomplete required blocks, unclosed frames, or balanced state with cursor depth.

Important boundaries:

- Curly braces are tracked outside comments, directives, continuations, escapes, and verbatim payloads.
- Square brackets are structural only in recognized `\def`, `\wait`, and `\guarded` decorator positions; arbitrary square brackets are not globally balanced.
- Depth counts curly-brace frames, not square-bracket decorators.
- `\text{}...\endtext{}` payloads are raw text. Inside them, normal completion is suppressed and only a matching `\endtext` candidate is offered.

A balanced badge therefore means only that this heuristic found no tracked issue.

### 5.3 Candidate interactions

| Trigger/context | Offered candidates |
|---|---|
| Bare or partial `\command` | Commands, keys, escapes, user macros; a bare `\` also includes the two layout presets |
| `\#` / `\#!...` | Comment template and syntax directives |
| `$` / `${` | `$ret`, `$err`, `$timeout`, built-in slot examples, and document-derived variables |
| `\click{`, `\mdown{`, `\mup{` | `L`, `R`, `M`, `B`, `F`; click count has a label but no candidates |
| `\kdown{`, `\kup{`, `\tap{` | Keys/modifiers; text after `+` is completed as a separate chord segment |
| `\use{` | `sys`, `net`, `img` |
| `\call{` | All seven registered frontend call candidates, including watch/unwatch |
| Predicate positions | `\all`, `\any`, triplet templates, variable operands, and comparison operators as applicable |
| `\move`, `\rgb`, call argument packs | Argument context labels without generic numeric candidates |
| Awaiting a required block | A block insertion template |

Candidates are clicked, not navigated with arrow keys or accepted through a separate Enter completion binding. A click replaces exactly the recorded token range, focuses the editor, records undo history, flashes the chip for **500ms**, and refreshes the inspector. With no matches, the tray shows its idle guide.

## 6. Editor Interactions Used by the Manual

| Input | Current behavior |
|---|---|
| `Ctrl/Cmd+S` | Activates the named-preset Save button |
| `Shift+Alt+F` | Formats the macro and records an immediate undo state |
| `Tab` | Inserts two spaces, replacing a single-line selection; indents every line of a multiline selection |
| `Shift+Tab` | Outdents affected lines from their line starts by two spaces, or one existing space/tab, adjusting the selection accordingly |
| `Enter` | Inherits indentation, adds two spaces after a trailing `{`, and expands an empty `{}`/`[]` pair into three lines |
| `{` / `[` | Auto-closes at a cursor or wraps selected text |
| `}` / `]` | Steps over an immediately following matching closer without inserting or creating history |
| `Backspace` in an empty pair | Deletes both delimiters |
| `Ctrl/Cmd+/` | Toggles `\#` comments on affected lines; uncommenting requires every nonempty line to be commented |

Bracket auto-close/wrap, closer overtyping, and empty-pair Backspace are disabled inside verbatim text. Enter expansion and comment toggling do not use that immunity rule.
