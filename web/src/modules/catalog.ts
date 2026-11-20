/**
 * Single Source of Truth (SSOT) DSL Catalog for CircuitMacro.
 * Unifies all macro specifications, AST signatures, LSP candidate dictionaries,
 * and the interactive Manual Drawer documentation into a single authoritative module.
 */

export type CandidateKind =
	| "cmd"
	| "key"
	| "var"
	| "slot"
	| "btn"
	| "ns"
	| "macro"
	| "syntax"
	| "escape"
	| "op";

export interface InspectorCandidate {
	label: string;
	insert: string;
	kind: CandidateKind;
	desc: string;
	tokenStart?: number;
	tokenEnd?: number;
}

export interface FormField {
	name: string;
	type: "select" | "text";
	options?: string[];
	default: string;
	placeholder?: string;
	width?: string;
}

export type RawField = [
	name: string,
	def: string,
	optOrPh?: string[] | string,
	width?: string
];

export interface ManualItem {
	name: string;
	syntax?: string;
	desc: string;
	snippet: string;
	aliases?: string[];
	usageId?: string;
	fields?: FormField[];
	template?: string;
	returns?: string;
	multiline?: boolean;
	keywords?: string;
	notes?: [string, string][];
	table?: { headers: string[]; rows: string[][] };
}

export interface ManualCategory {
	name: string;
	badge: string;
	desc?: string;
	items: ManualItem[];
}

export interface BlockRule {
	name: string;
	kind: "brace" | "bracket";
	required: boolean;
}

export interface MultiBlockSignature {
	cmd: string;
	rules: BlockRule[];
}

function item(
	name: string,
	syntax: string,
	desc: string,
	snippet: string,
	opts?: {
		aliases?: string[];
		usageId?: string;
		fields?: RawField[];
		template?: string;
		returns?: string;
		multiline?: boolean;
		keywords?: string;
		notes?: [string, string][];
		table?: { headers: string[]; rows: string[][] };
	}
): ManualItem {
	const it: ManualItem = { name, syntax, desc, snippet };
	if (opts) {
		if (opts.aliases) it.aliases = opts.aliases;
		if (opts.usageId) it.usageId = opts.usageId;
		if (opts.fields) {
			it.fields = opts.fields.map(([fName, def, optOrPh, width]) => ({
				name: fName,
				type: Array.isArray(optOrPh) ? "select" : "text",
				default: def,
				options: Array.isArray(optOrPh) ? optOrPh : undefined,
				placeholder: typeof optOrPh === "string" ? optOrPh : undefined,
				width
			}));
		}
		if (opts.template) it.template = opts.template;
		if (opts.returns) it.returns = opts.returns;
		if (opts.multiline) it.multiline = opts.multiline;
		if (opts.keywords) it.keywords = opts.keywords;
		if (opts.notes) it.notes = opts.notes;
		if (opts.table) it.table = opts.table;
	}
	return it;
}



// =========================================================================
// 1. Interactive Manual Drawer Category Data
// =========================================================================
export const MANUAL_DATA: ManualCategory[] = [
	{
		name: "Preamble & Syntax Layout Directives",
		badge: "Layout",
		desc: "Preamble directives controlling raw whitespace, newlines, and indentation absorption at the start of a script.",
		items: [
			item("Whitespace Behavior & Precedence Matrix", "Syntax Matrix", "Comparative resolution table for whitespace, comments, continuations, and verbatim text across layout modes.", "", {
				table: {
					headers: ["Entity", "typewriter (default)", "free layout", "Semantics"],
					rows: [
						["Space ' ' & Tab '\\t'", "Emits Space (0x20) / Tab (0x2B)", "Ignored (lexical delimiters)", "space=ignore/emit jointly controls both space and tab"],
						["Newline LF/CRLF", "Emits Return (0x28)", "Ignored (line separator)", "newline=ignore/emit controls physical return keystroke emission"],
						["Comment \\# text", "Stripped in preprocessor", "Stripped in preprocessor", "Highest precedence outside verbatim; treated as literal inside \\text{}"],
						["Continuation '\\' + newline", "Absorbs newline (no Return)", "Absorbs newline", "Folds logical statement lines across physical newlines"],
						["Verbatim block \\text{}", "Emits literal ASCII characters", "Emits literal ASCII characters", "Absolute precedence: comments, escapes, and variables are disabled"],
						["Escapes \\\\, \\{, \\}, \\$", "Emits literal symbol characters", "Emits literal symbol characters", "Escapes reserved syntax characters outside \\text{}"]
					]
				}
			}),
			item("Preset Layout Mode", "\\#!syntax: <preset>", "Selects script-wide whitespace policy: 'free' absorbs raw spaces and newlines as code structure; 'typewriter' emits Return (0x28) and Space (0x20) verbatim.", "\\#!syntax: free\n", {
				fields: [
					["preset", "free", ["free", "typewriter"], "100px"]
				],
				template: "\\#!syntax: ${preset}\n",
			}),
			item("Fine-Grained Whitespace Policy", "\\#!syntax: newline=<ignore|emit>, space=<ignore|emit>", "Configures newline and space handling independently, e.g. 'newline=ignore, space=emit' allows multiline code while preserving typed spaces.", "\\#!syntax: newline=ignore, space=emit\n", {
				fields: [
					["newline", "ignore", ["ignore", "emit"], "75px"],
					["space", "emit", ["emit", "ignore"], "75px"]
				],
				template: "\\#!syntax: newline=${newline}, space=${space}\n",
			}),
			item("Line Continuation (\\)", "\\ at line end", "A trailing backslash absorbs the newline without emitting Return. Used in typewriter mode to split commands across lines.", "\\\n")
		]
	},
	{
		name: "Special Escapes, Remarks & Verbatim Blocks",
		badge: "Syntax",
		desc: "Syntax escaping for reserved characters, non-emitting developer comments, and raw verbatim text payloads that bypass DSL parsing rules entirely.",
		items: [
			item("Verbatim Text Block (\\text)", "\\text{}<payload>\\endtext{}", "Types raw ASCII text verbatim without escaping. Disables all commands and variable interpolations except \\endtext{}.", "\\text{}echo \"Hello, World!\"\\endtext{}", {
				multiline: true,
			}),
			item("Single-Line Comment (\\#)", "\\# <text>", "Single-line remark stripped by compiler. Consumes text until newline and produces zero bytecode.", "\\# Step 1: initialize workflow\n", {
				fields: [
					["text", "Step 1: initialize workflow", "comment text...", "180px"]
				],
				template: "\\# ${text}\n",
			}),
			item("Literal Backslash (\\\\)", "\\\\", "Emits a single literal backslash character (0x5C) to the host system.", "\\\\"),
			item("Literal Curly Braces (\\{, \\})", "\\{ or \\}", "Emits literal curly brace characters without opening or closing DSL blocks or parameter scopes.", "\\{", {
				fields: [
					["brace", "{", ["{", "}"], "45px"]
				],
				template: "\\${brace}",
			}),
			item("Literal Dollar Sign (\\$)", "\\$", "Emits a literal dollar sign character ($) to the host without triggering variable or slot interpolation ($var / ${var}).", "\\$")
		]
	},
	{
		name: "Physical Keyboard: Standard Keys & Taps",
		badge: "Keyboard",
		desc: "Single physical keystrokes, navigation, editing, and functional keys from USB HID Usage Table (Page 0x07) and Consumer Audio Control (Page 0x0C).",
		items: [
			item("Explicit Key Tap (\\tap)", "\\tap{<key>}", "Executes an atomic key press, hold delay, and key release for a physical key. Recommended in free layout mode to make single keystroke actions explicit and structured.", "\\tap{enter}", {
				fields: [
					["key", "enter", [
						"enter", "tab", "space", "esc", "bs", "del", "ins",
						"up", "down", "left", "right", "home", "end", "pgup", "pgdn",
						"minus", "equal", "slash", "backslash", "semicolon", "quote",
						"f1", "f5", "f12", "caps", "kp_enter", "volup", "media_playpause"
					], "95px"]
				],
				template: "\\tap{${key}}",
			}),
			item("Editing & Whitespace Keys", "\\enter, \\tab, \\space, \\bs, \\del, \\ins", "Direct command aliases for editing keys. In free layout, each key command emits its canonical keystroke.", "\\enter", {
				fields: [
					["key", "enter", ["enter", "tab", "space", "bs", "del", "ins", "esc"], "85px"]
				],
				template: "\\${key}",
				aliases: ["return", "escape", "backspace", "delete", "insert"],
			}),
			item("Directional & Cursor Navigation", "\\up, \\down, \\left, \\right, \\home, \\end, \\pgup, \\pgdn", "Cursor navigation and paging keys from USB HID Page 0x07.", "\\up", {
				fields: [
					["nav", "up", ["up", "down", "left", "right", "home", "end", "pgup", "pgdn"], "85px"]
				],
				template: "\\${nav}",
				aliases: ["pageup", "pagedown"],
				usageId: "0x4A..0x52",
			}),
			item("Punctuation & Symbols (Page 0x07)", "\\minus, \\equal, \\slash, \\backslash, \\semicolon, \\quote", "Standard keyboard punctuation and symbol keys from USB HID Page 0x07.", "\\minus", {
				fields: [
					["key", "minus", ["minus", "equal", "bracketleft", "bracketright", "backslash", "semicolon", "quote", "grave", "comma", "period", "slash"], "95px"]
				],
				template: "\\${key}",
				aliases: ["dot"],
				usageId: "0x2D..0x38",
			}),
			item("Top-Row Function Keys (F1-F12)", "\\f1..\\f12", "Standard top-row function keys (F1=0x3A .. F12=0x45) mapped to standard PC HID usage codes.", "\\f1", {
				fields: [
					["fn", "f1", Array.from({ length: 12 }, (_, i) => `f${i + 1}`), "65px"]
				],
				template: "\\${fn}",
				usageId: "0x3A..0x45",
			}),
			item("Extended Function Keys (F13-F24)", "\\f13..\\f24", "Extended productivity and workstation function keys (F13=0x68 .. F24=0x73). Widely used in macro scripts for conflict-free hotkeys.", "\\f13", {
				fields: [
					["fn", "f13", Array.from({ length: 12 }, (_, i) => `f${i + 13}`), "65px"]
				],
				template: "\\${fn}",
				usageId: "0x68..0x73",
			}),
			item("Keypad & Numpad Keys", "\\kp_enter, \\kp_plus, \\kp_minus, \\kp_0..\\kp_9", "Dedicated numeric keypad and operator keys (0x54..0x67). Operates independently of top-row digits.", "\\kp_enter", {
				fields: [
					["kp", "kp_enter", ["kp_enter", "kp_plus", "kp_minus", "kp_multiply", "kp_divide", "kp_dot", "kp_equal", ...Array.from({ length: 10 }, (_, i) => `kp_${i}`)], "105px"]
				],
				template: "\\${kp}",
				aliases: ["kp_star", "kp_slash", "kp_period"],
				usageId: "0x54..0x67",
			}),
			item("Locks, System & Application Keys", "\\caps, \\numlock, \\prtscr, \\pause, \\menu", "Hardware lock and system keys: Caps Lock (0x39), Num Lock (0x53), Scroll Lock (0x47), Print Screen (0x46), Pause (0x48), Menu (0x65).", "\\caps", {
				fields: [
					["key", "caps", ["caps", "numlock", "scrlk", "prtscr", "pause", "menu"], "95px"]
				],
				template: "\\${key}",
				aliases: ["capslock", "scrolllock", "printscreen", "snapshot", "break", "app", "contextmenu"],
				usageId: "0x39..0x65",
			}),
			item("Consumer Media & Audio Keys", "\\volup, \\voldn, \\mute, \\media_playpause", "USB Consumer Control page 0x0C multimedia keys. Emulated over a dedicated report descriptor; recognized across Windows, macOS, Linux, and Android.", "\\volup", {
				fields: [
					["media", "volup", ["volup", "voldn", "mute", "media_play", "media_pause", "media_playpause", "media_next", "media_prev", "media_stop"], "130px"]
				],
				template: "\\${media}",
				usageId: "0x0C:0xE9..0xCD",
			})
		]
	},
	{
		name: "Modifiers & Key Chording",
		badge: "Modifiers",
		desc: "Modifier key prefixes, chording combinations connected with +, and persistent press/release holds. The VM guarantees all held keys and mouse buttons are safely released upon macro termination or error.",
		items: [
			item("Modifier Tap & Prefix", "\\ctrl, \\shift, \\alt, \\cmd", "Tap a single modifier key alone or use as a prefix in combinations. Supports left/right physical variants (lctrl, rctrl, lshift, rshift, lalt, ralt, rgui).", "\\ctrl", {
				fields: [
					["mod", "ctrl", ["ctrl", "shift", "alt", "cmd", "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt", "gui", "win"], "75px"]
				],
				template: "\\${mod}",
				aliases: ["control", "option", "command", "windows"],
			}),
			item("Shortcut Combinations (\\mod+key)", "\\ctrl+c, \\cmd+v, \\alt+tab", "Atomic shortcut chords pressed and released in hardware sequence: modifier held, base key tapped, modifier released. Connect keys with +.", "\\ctrl+c", {
				fields: [
					["mod", "ctrl", ["ctrl", "cmd", "alt", "shift", "ctrl+shift", "cmd+shift", "alt+shift", "ctrl+alt"], "85px"],
					["key", "c", ["c", "v", "z", "a", "s", "x", "f", "w", "t", "r", "tab", "space", "enter", "esc", "f5"], "65px"]
				],
				template: "\\${mod}+${key}",
			}),
			item("Key Hold Down (\\kdown)", "\\kdown{<key>}", "Presses and holds key down without releasing. Supports single keys, modifiers, and composite chords (e.g. \\kdown{ctrl+shift}). Must be paired with \\kup when finished.", "\\kdown{ctrl}", {
				fields: [
					["key", "ctrl", "key name", "80px"]
				],
				template: "\\kdown{${key}}",
			}),
			item("Key Release Up (\\kup)", "\\kup{[<key>]}", "Releases held key. Empty braces \\kup{} safely releases all keys currently held down on the keyboard.", "\\kup{ctrl}", {
				fields: [
					["key", "ctrl", "empty for all", "80px"]
				],
				template: "\\kup{${key}}",
			})
		]
	},
	{
		name: "Mouse Actions & Pointer Movement",
		badge: "Mouse",
		desc: "Mouse clicks, multi-click repeats, drag-holds, and relative cursor displacement. Mouse coordinates are relative (dx, dy). Large movements (>127) are automatically chunked into sequential HID reports by the driver.",
		items: [
			item("Click Mouse Button (\\click)", "\\click{<btn> [, <count>]}", "Clicks specified mouse button: L (left), R (right), M (middle), B (back/X1), or F (forward/X2). Optional count parameter repeats the click with a 20ms debounce delay.", "\\click{L}", {
				fields: [
					["btn", "L", ["L", "R", "M", "B", "F"], "50px"],
					["count", "1", "count", "45px"]
				],
				template: "\\click{${btn}${count?, $val}}",
			}),
			item("Relative Pointer Move (\\move)", "\\move{<dx>, <dy>}", "Displaces mouse pointer by signed dx, dy offsets (-32768..32767). Movements are relative to the current cursor position.", "\\move{100, -50}", {
				fields: [
					["dx", "100", "dx", "55px"],
					["dy", "-50", "dy", "55px"]
				],
				template: "\\move{${dx}, ${dy}}",
			}),
			item("Mouse Button Down (\\mdown)", "\\mdown{<btn>}", "Presses and holds a mouse button down for drag-and-drop or long-press gestures. Must be paired with \\mup to release.", "\\mdown{L}", {
				fields: [
					["btn", "L", ["L", "R", "M", "B", "F"], "50px"]
				],
				template: "\\mdown{${btn}}",
			}),
			item("Mouse Button Up (\\mup)", "\\mup{<btn>}", "Releases a held mouse button to complete a drag-and-drop or click gesture.", "\\mup{L}", {
				fields: [
					["btn", "L", ["L", "R", "M", "B", "F"], "50px"]
				],
				template: "\\mup{${btn}}",
			})
		]
	},
	{
		name: "Execution Timing & Action Pacing",
		badge: "Timing",
		desc: "Explicit pause delays and global inter-action keystroke pacing intervals. Delays yield cooperatively in 20ms slices, ensuring concurrent HTTP polling and raw TCP channels remain fully responsive.",
		items: [
			item("Sleep / Delay (\\delay)", "\\delay{<duration>}", "Pauses macro execution for specified duration (e.g. 100ms, 2s). Yields cooperatively in 20ms slices without blocking network I/O or HTTP status polling.", "\\delay{100ms}", {
				fields: [
					["duration", "100ms", "e.g. 50ms, 1s", "90px"]
				],
				template: "\\delay{${duration}}",
			}),
			item("Action Pacing (\\pace)", "\\pace{<duration>}", "Sets the global minimum interval injected between subsequent keystrokes and clicks across the entire macro. Matches host OS keyboard repeat and input polling thresholds.", "\\pace{20ms}", {
				fields: [
					["pacing", "20ms", "e.g. 10ms, 50ms", "90px"]
				],
				template: "\\pace{${pacing}}",
			})
		]
	},
	{
		name: "Variables, Registers & Arithmetic",
		badge: "Registers",
		desc: "32 signed 32-bit integer registers ($r0..$r31) with wrapping arithmetic and decimal keystroke rendering. (See 'Runtime Values & Resources'.)",
		items: [
			item("Assign Variable (\\set)", "\\set{<var>}{<val>}", "Allocates a general register and assigns a signed 32-bit integer or object reference into $var.", "\\set{x}{100}", {
				fields: [
					["var", "x", "var name", "60px"],
					["val", "100", "value", "70px"]
				],
				template: "\\set{${var}}{${val}}",
			}),
			item("Add to Variable (\\add)", "\\add{<var>}{<val>}", "Performs in-place addition on general register $var ($var = $var + val). Wraps around standard i32 bounds.", "\\set{x}{0}\\add{x}{1}", {
				fields: [
					["var", "x", "var name", "60px"],
					["val", "1", "increment", "70px"]
				],
				template: "\\add{${var}}{${val}}",
			}),
			item("Subtract from Variable (\\sub)", "\\sub{<var>}{<val>}", "Performs in-place subtraction on general register $var ($var = $var - val).", "\\set{x}{10}\\sub{x}{1}", {
				fields: [
					["var", "x", "var name", "60px"],
					["val", "1", "decrement", "70px"]
				],
				template: "\\sub{${var}}{${val}}",
			}),
			item("Render Number as Keystrokes (\\typenum)", "\\typenum{<var_ref>}", "Converts the integer value stored in a register to decimal ASCII digits and types them sequentially to the host.", "\\set{x}{42}\\typenum{$x}", {
				fields: [
					["var", "$x", "variable", "70px"]
				],
				template: "\\typenum{${var}}",
			}),
			item("Read-Only Status Registers", "$ret, $err, $timeout", "Status registers updated by VM operations: $ret holds call return value or handle; $err holds error code (0=OK); $timeout holds wait latch flag.", "$ret", {
				fields: [
					["reg", "$ret", ["$ret", "$err", "$timeout"], "85px"]
				],
				template: "${reg}",
			})
		]
	},
	{
		name: "Control Flow & Guarded Invariants",
		badge: "Control",
		desc: "Relational comparisons (=, !=, <, <=, >, >=), compound logic (\\all, \\any), and guarded invariant blocks. (See 'Execution & Event Semantics'.)",
		items: [
			item("Conditional Branch (\\if)", "\\if{<predicate>}{<then>}[{<else>}]", "Evaluates a predicate comparison triplet or logic block, executing the then-block if true or optional else-block if false.", "\\if{ {$ret, =, 0} }{\n  \\enter\n}\n", {
				fields: [
					["lhs", "$ret", "lhs", "50px"],
					["op", "=", ["=", "!=", "<", "<=", ">", ">="], "45px"],
					["rhs", "0", "rhs", "50px"]
				],
				template: "\\if{ {${lhs}, ${op}, ${rhs}} }{\n  \\enter\n}\n",
				multiline: true,
			}),
			item("Conjunction Logic Block (\\all)", "\\all{ {p1}, {p2} }", "Logical AND conjunction: passes only if every inner comparison triplet evaluates to true.", "\\set{x}{50}\\if{ \\all{ {$x, >=, 0}, {$x, <=, 100} } }{\\enter}"),
			item("Disjunction Logic Block (\\any)", "\\any{ {p1}, {p2} }", "Logical OR disjunction: passes if at least one inner comparison triplet evaluates to true.", "\\if{ \\any{ {$err, !=, 0}, {$timeout, =, 1} } }{\\esc}"),
			item("Guarded Invariant Block (\\guarded)", "\\guarded[<throttle>]{<invariant>}{<body>}[{<on_abort>}]", "Continuously asserts invariant predicate. On violation, halts body, releases held keys/buttons, and runs optional on_abort block.", "\\guarded[50ms]{ {$err, =, 0} }{\n  \\click{L}\n  \\delay{50ms}\n}{\n  \\esc\n}\n", {
				fields: [
					["throttle", "50ms", "throttle interval", "80px"]
				],
				template: "\\guarded[${throttle}]{ {$err, =, 0} }{\n  \\click{L}\n  \\delay{50ms}\n}{\n  \\esc\n}\n",
				multiline: true,
			})
		]
	},
	{
		name: "Loops & User Subroutines",
		badge: "Subroutines",
		desc: "Bounded counting loops and compile-time parameterized macro definitions. Loops and macro expansions run on an explicit register machine with zero Python recursion, safely bounded only by bytecode capacity.",
		items: [
			item("Repeat Counting Loop (\\rep)", "\\rep{<count>}{<body>}", "Bounded counting loop repeating body 0..65535 times. Runs via jump offsets without allocating Python call stack frames.", "\\rep{10}{\n  \\click{L}\n  \\delay{50ms}\n}\n", {
				fields: [
					["count", "10", "count", "50px"]
				],
				template: "\\rep{${count}}{\n  \\click{L}\n  \\delay{50ms}\n}\n",
				multiline: true,
			}),
			item("Define Parameterized Macro (\\def)", "\\def{<name>}[N]{<body>}", "Defines a reusable compile-time macro with optional parameter substitution #1..#9. Macro calls expand inline at the point of invocation.", "\\def{press_key}[1]{\\kdown{#1}\\delay{20ms}\\kup{#1}}\n\\press_key{enter}\n", {
				fields: [
					["name", "press_key", "name", "85px"],
					["argc", "1", ["0", "1", "2", "3"], "45px"]
				],
				template: "\\def{${name}}[${argc}]{\\kdown{#1}\\delay{20ms}\\kup{#1}}\n",
				multiline: true,
			})
		]
	},
	{
		name: "Asynchronous Signals & Slots ($slot, \\wait)",
		badge: "Signals",
		desc: "Asynchronous event subscription and suspension primitives. Macros wait on slots via \\wait without busy-polling. (See 'Execution & Event Semantics'.)",
		items: [
			item("Wait for Signal Event (\\wait)", "\\wait[<timeout>]{ $slot.<name> }", "Suspends execution until an event arrives at specified slot or timeout expires. Sets $timeout=0 on arrival, 1 on timeout.", "\\use{net}\\wait[5s]{ $slot.net.msg.val }", {
				fields: [
					["timeout", "5s", "timeout", "55px"],
					["slot", "net.msg.val", "slot name", "100px"]
				],
				template: "\\wait[${timeout}]{ $slot.${slot} }",
			}),
			item("Signal Slot References ($slot)", "$slot.<sig>.<field>", "Read-only receptor slot holding the latest value broadcast by an asynchronous signal (e.g. $slot.net.msg.val, $slot.img.hp.val). Reading before first arrival yields 0.", "$slot.net.msg.val", {
				fields: [
					["slot", "net.msg.val", "slot name", "110px"]
				],
				template: "$slot.${slot}",
			})
		]
	},
	{
		name: "Extension Namespace: sys (System & Diagnostics)",
		badge: "sys",
		desc: "Core firmware diagnostics, memory monitoring, and runtime execution environment. Always declare \\use{sys} once before calling any sys.* functions in your macro.",
		items: [
			item("Activate Namespace (\\use{sys})", "\\use{sys}", "Declares dependency on the sys namespace, binding system diagnostic functions into the macro execution context.", "\\use{sys}\n"),
			item("Query Free Heap Memory (sys.free)", "\\call{sys.free}", "Queries CircuitPython GC allocator free heap bytes. Writes free memory byte count to $ret. Essential for asserting device memory headroom before processing large image frames.", "\\use{sys}\n\\call{sys.free}", {
				returns: "$ret (free bytes in RAM)",
			})
		]
	},
	{
		name: "Extension Namespace: net (Host Network Channel)",
		badge: "net",
		desc: "Raw bi-directional TCP communication channel (:7373) connecting the macro device to host companion applications. Enables bidirectional handshakes, message broadcasting, and event-driven automation.",
		items: [
			item("Activate Namespace (\\use{net})", "\\use{net}", "Declares dependency on the net namespace, activating network broadcast and signal receptors.", "\\use{net}\n"),
			item("Broadcast Message to Host (net.send)", "\\call{net.send}{<msg>}", "Broadcasts message tag to all authenticated companion clients on raw TCP port (:7373). Writes status code to $err (0=ERR_OK).", "\\use{net}\n\\call{net.send}{ready}", {
				fields: [
					["msg", "ready", "message tag", "120px"]
				],
				template: "\\use{net}\n\\call{net.send}{${msg}}",
			}),
			item("Await Host Message Arrival", "\\wait[<timeout>]{ $slot.net.msg.val }", "Suspends execution until a host client sends a message to the device or timeout expires.", "\\use{net}\\wait[5s]{ $slot.net.msg.val }", {
				fields: [
					["timeout", "5s", "timeout", "55px"]
				],
				template: "\\use{net}\\wait[${timeout}]{ $slot.net.msg.val }",
			})
		]
	},
	{
		name: "Extension Namespace: img (Screen Telemetry & Vision)",
		badge: "img",
		desc: "Screen capture, point color sampling, diffing, and streaming telemetry coordinated with an external capture provider (e.g. OBS Studio).",
		items: [
			item("Activate Namespace (\\use{img})", "\\use{img}", "Declares dependency on the img namespace, activating screen capture, pixel sampling, and stream telemetry verbs.", "\\use{img}\n"),
			item("Capture Snapshot Region (img.snap)", "\\call{img.snap}{<x>, <y>, <w>, <h>}", "Captures rectangular screen region from external provider. Allocates snapshot handle in device memory and writes handle ID to $ret.", "\\use{img}\n\\call{img.snap}{0, 0, 100, 100}", {
				fields: [
					["x", "0", "x", "45px"],
					["y", "0", "y", "45px"],
					["w", "100", "w", "45px"],
					["h", "100", "h", "45px"]
				],
				template: "\\use{img}\n\\call{img.snap}{${x}, ${y}, ${w}, ${h}}",
				returns: "$ret (snap handle ID)",
			}),
			item("Sample Pixel Color (img.at)", "\\call{img.at}{$snap}{<x>, <y>}", "Inspects packed 24-bit RGB color (0xRRGGBB) at specified coordinate within captured snap handle. Writes packed color integer to $ret.", "\\use{img}\n\\call{img.snap}{0, 0, 100, 100}\n\\set{snap}{$ret}\n\\call{img.at}{$snap}{50, 50}", {
				fields: [
					["snap", "$snap", "handle", "55px"],
					["x", "50", "x", "45px"],
					["y", "50", "y", "45px"]
				],
				template: "\\call{img.at}{${snap}}{${x}, ${y}}",
				returns: "$ret (packed 0xRRGGBB)",
			}),
			item("Compare Snapshots (img.diff)", "\\call{img.diff}{$a}{$b}{<tol>}", "Computes mismatching pixel count between two snapshot handles within per-channel color tolerance (0..255). Writes delta count to $ret.", "\\use{img}\n\\call{img.snap}{0, 0, 10, 10}\n\\set{a}{$ret}\n\\call{img.snap}{10, 10, 10, 10}\n\\set{b}{$ret}\n\\call{img.diff}{$a}{$b}{16}", {
				fields: [
					["a", "$a", "snap a", "50px"],
					["b", "$b", "snap b", "50px"],
					["tol", "16", "tol", "45px"]
				],
				template: "\\call{img.diff}{${a}}{${b}}{${tol}}",
				returns: "$ret (delta count; 0 = identical)",
			}),
			item("Watch Stream Telemetry (img.watch)", "\\call{img.watch}{<name>}{<x, y>}{<interval, duration>}", "Registers continuous stream telemetry observer on external capture provider. Pushes sampled color frames into $slot.img.<name>.val at specified interval.", "\\use{img}\n\\call{img.watch}{hp}{100, 50}{50ms, 5s}", {
				fields: [
					["name", "hp", "signal name", "60px"],
					["coord", "100, 50", "x, y", "70px"],
					["span", "50ms, 5s", "interval, duration", "85px"]
				],
				template: "\\use{img}\n\\call{img.watch}{${name}}{${coord}}{${span}}",
			}),
			item("Stop Watcher (img.unwatch)", "\\call{img.unwatch}{<name>}", "Cancels active stream telemetry observer on external provider and unregisters local signal.", "\\use{img}\n\\call{img.unwatch}{hp}", {
				fields: [
					["name", "hp", "signal name", "60px"]
				],
				template: "\\use{img}\n\\call{img.unwatch}{${name}}",
			})
		]
	},
	{
		name: "Literal Constructors & Matrix Declarations",
		badge: "Literals",
		desc: "Compile-time immediate packed 24-bit RGB scalars and pixel matrix literals. Pixel matrix literals embed reference patterns directly into firmware memory for zero-allocation matching with img.diff.",
		items: [
			item("Pack RGB Color (\\rgb)", "\\rgb{<r>, <g>, <b>}", "Packs red, green, blue intensities (0..255) into a 24-bit scalar immediate (0xRRGGBB).", "\\set{col}{\\rgb{255, 0, 0}}", {
				fields: [
					["r", "255", "r", "40px"],
					["g", "0", "g", "40px"],
					["b", "0", "b", "40px"]
				],
				template: "\\rgb{${r}, ${g}, ${b}}",
			}),
			item("Pixel Matrix Literal (\\pixelrect)", "\\pixelrect{<name>}{\\row{<colors>}}", "Materializes a multi-row pixel pattern as a snapshot handle in the const arena.", "\\pixelrect{pat}{\n  \\row{0xFF0000, 0x00FF00}\n  \\row{0x0000FF, 0xFFFFFF}\n}\n", {
				multiline: true,
			})
		]
	},
	{
		name: "Execution & Event Semantics",
		badge: "Semantics",
		desc: "Normative runtime execution model governing event arrivals, slot synchronization, guarded invariants, and interruptible delays.",
		items: [
			item("Event Latching & Slot Model", "Event & Slot Norms", "Authoritative rules for event arrival latching, coalescing, lossy buffering, and timeout determinism.", "", {
				notes: [
					["Arrival Latching", "\\wait suspends until an event arrival (pending latch) is set, not on value change; identical values still wake up."],
					["Pre-arrival Delivery", "Events arriving before \\wait latch pending=1 immediately; \\wait consumes the latch instantly with $timeout=0."],
					["Lossy Coalescing", "Each slot has one {val, pending} receptor (MAX_SLOTS=256), not a FIFO queue. Rapid bursts overwrite val with newest payload; unconsumed events coalesce into a single wakeup, lossily dropping intermediate values."],
					["Telemetry Streaming", "During img.watch, \\wait wakes on arrived ticks to read latest color. Intervening ticks coalesce when the macro is busy, always yielding the freshest frame."],
					["Timeout Determinism", "When specified duration elapses without an arrival, the VM sets $timeout=1 and preserves previous slot value."]
				]
			}),
			item("Guarded Invariant & Slicing", "Guarded Norms", "Authoritative rules for entry evaluation, check frequency, latency bounds, delay slicing, and abort semantics.", "", {
				notes: [
					["Entry Evaluation", "When entering \\guarded, if the invariant evaluates to false, the body block is never entered and execution branches immediately."],
					["Check Frequency & Throttle", "At throttle=0 (default), invariants evaluate at every instruction boundary. At throttle>0 (e.g. 50ms), checks run at boundaries where elapsed time >= throttle."],
					["Latency & Delay Slicing", "\\delay and pacing slice into micro-slices of min(throttle, 20ms). Latency is bounded by throttle (or 20ms at throttle=0) plus active atomic HID/call time."],
					["Unguarded Abort Context", "On invariant failure, the VM releases keys/buttons (0x1F), rolls back loop depth, and exits the guarded scope before jumping to on_abort, preventing recursive abort traps."]
				]
			})
		]
	},
	{
		name: "Runtime Values & Resources",
		badge: "Runtime",
		desc: "Normative data model governing 32-bit registers, opaque resource handles, lifetime reference tracking, and extension call conventions.",
		items: [
			item("Register File & System Status", "Register Norms", "32 general-purpose signed i32 / Handle registers, static variable mapping, and status registers.", "", {
				notes: [
					["General Registers", "32 physical registers ($r0..$r31) hold signed 32-bit integers (modulo-2^32 wrapping) or opaque Handle IDs."],
					["Static Variable Mapping", "Symbolic variables ($x, $snap) statically map to $r0..$r31 in order of first appearance (max 32). Exceeding 32 distinct variables triggers a compile error."],
					["Handle Isolation & Faults", "Arithmetic and comparisons on Handles trigger a non-fatal Handle Fault: operation is skipped, macro continues, and $err is set to 8 (ERR_HANDLE_MISUSE)."],
					["Return Slot ($ret)", "Holds \\call result or allocated Handle ID (0 on error). Retains a handle reference until overwritten by a subsequent \\call."],
					["Error Slot ($err)", "Non-fatal status code: 0=OK, 1=GENERIC, 2=INVALID_ARG, 3=NO_PROVIDER, 4=OUT_OF_BOUNDS, 5=HANDLE_INVALID, 6=RESOURCE_EXHAUSTED, 7=TIMEOUT, 8=HANDLE_MISUSE."],
					["Timeout Slot ($timeout)", "Wait completion status (0=event arrived, 1=timed out); non-fatal."]
				]
			}),
			item("Handle Lifecycle & Copy Tracking", "Handle Lifecycle", "Handle reference tracking across registers, non-destructive overwrites, and automatic PSRAM release.", [
				"\\use{img}",
				"\\use{sys}",
				"\\call{img.snap}{0, 0, 100, 100}  \\# allocates handle; $ret (0xFF) holds 1st ref",
				"\\set{a}{$ret}   \\# $a copies handle reference; active refcount = 2 ($ret, $a)",
				"\\set{b}{$a}     \\# $b copies handle reference; active refcount = 3 ($ret, $a, $b)",
				"\\call{sys.free} \\# next \\call overwrites $ret with integer; refcount drops to 2 ($a, $b)",
				"\\set{a}{0}      \\# overwrite $a; $b still retains handle; refcount = 1 (buffer preserved)",
				"\\call{img.at}{$b}{50, 50}  \\# $b is valid; returns pixel color into $ret",
				"\\set{b}{0}      \\# active refcount drops to 0; PSRAM buffer automatically freed"
			].join("\n"), {
				multiline: true
			}),
			item("Universal Extension Call Convention", "Call Conventions", "Synchronous execution, argument validation, $ret/$err convention, and recoverable error handling.", "", {
				notes: [
					["Synchronous Execution", "The VM executes extension functions in-place, validating argument count and operand types before dispatch."],
					["Return Convention", "On success $err=0 and $ret holds the returned value; on failure $err holds the error code and $ret=0."],
					["Non-Fatal Semantics", "Extension call errors never crash or abort macro execution; control flow continues to the next instruction."],
					["Error Recovery", "Inspect status with conditional logic: \\call{net.send}{msg} \\if{ {$err, !=, 0} }{ \\# recovery action }"],
					["Safety Guarding", "Wrap critical operations inside \\guarded[50ms]{ {$err, =, 0} } to automatically trigger safety abort on failure."]
				]
			})
		]
	},
	{
		name: "Practical Recipes",
		badge: "Recipes",
		desc: "Ready-to-use tested macro automation workflows demonstrating complete real-world patterns with synchronization, invariants, and companion coordination.",
		items: [
			item("Rapid Mouse Clicker Loop", "Rapid Clicker Recipe", "Clicks left mouse button 50 times with 10ms pacing in free layout.", "\\#!syntax: free\n\\pace{10ms}\n\\rep{50}{\n  \\click{L}\n}\n", {
				multiline: true,
			}),
			item("Guarded Clipboard Copy-Paste", "Guarded Copy-Paste Recipe", "Guarded shortcut execution. If any fault occurs, all keys are safely released before running the abort recovery block.", "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n  \\delay{50ms}\n  \\ctrl+v\n}{\n  \\kup{}\n}\n", {
				multiline: true,
			}),
			item("Pixel Color Inspection & Branch", "Pixel Branch Recipe", "Captures screen region, checks pixel green intensity, clicks if verified.", "\\#!syntax: free\n\\use{img}\n\\call{img.snap}{100, 100, 50, 50}\n\\set{snap}{$ret}\n\\call{img.at}{$snap}{25, 25}\n\\set{col}{$ret}\n\\if{ {$col, =, 0x00FF00} }{\n  \\click{L}\n}\n", {
				multiline: true,
			}),
			item("Stream Telemetry Observer Wait", "Stream Watcher Recipe", "Watches point coord and waits for signal update latch before unwatching.", "\\#!syntax: free\n\\use{img}\n\\call{img.watch}{btn}{200, 150}{50ms, 10s}\n\\wait[10s]{ $slot.img.btn.val }\n\\call{img.unwatch}{btn}\n", {
				multiline: true,
			}),
			item("Dynamic Keystroke Counter Loop", "Counter Typing Recipe", "Loops and types numbers 1 to 10 as ASCII digits.", "\\set{x}{1}\n\\rep{10}{\n  \\typenum{$x}\n  \\space\n  \\add{x}{1}\n}\n", {
				multiline: true,
			})
		]
	}
];

// =========================================================================
// 2. LSP Inspector Candidate Dictionaries (Derived / Canonical)
// =========================================================================
const CC_RAW: [string, string, string, CandidateKind?][] = [
	["\\set", "\\set{x}{100}", "Declare & assign variable"],
	["\\add", "\\add{x}{1}", "In-place addition"],
	["\\sub", "\\sub{x}{1}", "In-place subtraction"],
	["\\click", "\\click{L}", "Click mouse button [L/R/M/B/F]"],
	["\\move", "\\move{100, -50}", "Relative mouse movement dx, dy"],
	["\\mdown", "\\mdown{L}", "Press and hold mouse button"],
	["\\mup", "\\mup{L}", "Release held mouse button"],
	["\\delay", "\\delay{100ms}", "Explicit sleep duration"],
	["\\pace", "\\pace{20ms}", "Set VM inter-action pacing"],
	["\\rep", "\\rep{10}{\n  \\click{L}\n  \\delay{50ms}\n}\n", "Bounded repeat loop"],
	["\\if", "\\if{ {$x, >=, 0} }{\n  \\enter\n}\n", "Conditional branch"],
	["\\all", "\\all{ {$x, >=, 0}, {$x, <=, 100} }", "Conjunction AND logic block"],
	["\\any", "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", "Disjunction OR logic block"],
	["\\guarded", "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n}{\n  \\esc\n}\n", "Guarded protected execution block"],
	["\\wait", "\\wait[5s]{ $slot.net.msg.val }", "Wait for signal arrival latch"],
	["\\def", "\\def{my_macro}[0]{\\enter}\n", "Define compile-time text macro"],
	["\\use", "\\use{sys}\n", "Activate runtime namespace"],
	["\\call", "\\call{sys.free}", "Invoke runtime extension function"],
	["\\typenum", "\\typenum{$x}", "Render decimal integer as keystrokes"],
	["\\text", "\\text{}payload\\endtext{}", "Verbatim text keystroke block"],
	["\\endtext", "\\endtext{}", "End verbatim text block"],
	["\\rgb", "\\rgb{255, 0, 0}", "Pack 24-bit RGB scalar literal"],
	["\\pixelrect", "\\pixelrect{target}{\\row{0xFF0000, 0x00FF00}}", "Materialize snapshot pixel matrix"],
	["\\row", "\\row{0xFF0000, 0x00FF00}", "Pixel row color vector"],
	["\\kdown", "\\kdown{ctrl}", "Press and hold key down"],
	["\\kup", "\\kup{ctrl}", "Release key (empty {} for all keys)"],
	["\\tap", "\\tap{enter}", "Single key or combination tap"],
	["\\enter", "Return / Enter key (alias: \\return)", "key"],
	["\\return", "Alias for \\enter (Usage 0x28)", "key"],
	["\\esc", "Escape key (alias: \\escape)", "key"],
	["\\escape", "Alias for \\esc (Usage 0x29)", "key"],
	["\\tab", "Tab key (Usage 0x2B)", "key"],
	["\\space", "Spacebar character tap", "key"],
	["\\bs", "Backspace key (alias: \\backspace)", "key"],
	["\\backspace", "Alias for \\bs (Usage 0x2A)", "key"],
	["\\del", "Forward Delete key (alias: \\delete)", "key"],
	["\\delete", "Alias for \\del (Usage 0x4C)", "key"],
	["\\ins", "Insert key (alias: \\insert)", "key"],
	["\\insert", "Alias for \\ins (Usage 0x49)", "key"],
	["\\home", "Home navigation key", "key"],
	["\\end", "End navigation key", "key"],
	["\\pgup", "Page Up key (alias: \\pageup)", "key"],
	["\\pageup", "Alias for \\pgup (Usage 0x4B)", "key"],
	["\\pgdn", "Page Down key (alias: \\pagedown)", "key"],
	["\\pagedown", "Alias for \\pgdn (Usage 0x4E)", "key"],
	["\\up", "Arrow Up key", "key"],
	["\\down", "Arrow Down key", "key"],
	["\\left", "Arrow Left key", "key"],
	["\\right", "Arrow Right key", "key"],
	["\\caps", "Caps Lock key (alias: \\capslock)", "key"],
	["\\capslock", "Alias for \\caps (Usage 0x39)", "key"],
	["\\prtscr", "Print Screen key (Usage 0x46)", "key"],
	["\\ctrl+c", "Copy clipboard shortcut", "key"],
	["\\ctrl+v", "Paste clipboard shortcut", "key"],
	["\\ctrl+z", "Undo shortcut", "key"],
	["\\ctrl+a", "Select All shortcut", "key"],
	["\\ctrl+s", "Save document shortcut", "key"],
	["\\cmd+space", "Spotlight / Launcher shortcut", "key"],
	["\\alt+tab", "Switch application window", "key"],
	["\\volup", "Volume increment (Consumer 0x00E9)", "key"],
	["\\voldn", "Volume decrement (Consumer 0x00EA)", "key"],
	["\\mute", "Audio mute toggle (Consumer 0x00E2)", "key"],
	["\\media_playpause", "Media Play/Pause (Consumer 0x00CD)", "key"],
	["\\media_play", "Media Play (Consumer 0x00B0)", "key"],
	["\\media_pause", "Media Pause (Consumer 0x00B1)", "key"],
	["\\media_next", "Media Next track (Consumer 0x00B5)", "key"],
	["\\media_prev", "Media Previous track (Consumer 0x00B6)", "key"],
	["\\media_stop", "Media Stop track (Consumer 0x00B7)", "key"],
	["\\brightup", "Brightness increment (Consumer 0x006F)", "key"],
	["\\brightdn", "Brightness decrement (Consumer 0x0070)", "key"],
	["\\sys_sleep", "System Sleep / Suspend (Consumer 0x0032)", "key"]
];
export const COMMAND_CANDIDATES: InspectorCandidate[] = CC_RAW.map(([l, a, b, c]) => {
	if (c) return { label: l, insert: a, desc: b!, kind: c };
	if (b === "key") return { label: l, insert: l, desc: a, kind: "key" };
	return { label: l, insert: a, desc: b!, kind: "cmd" };
});

export const SYNTAX_CANDIDATES: InspectorCandidate[] = [
	["\\#!syntax: free", "Free-form layout (absorbs raw whitespace)"],
	["\\#!syntax: typewriter", "Typewriter layout (raw Enter/Space emit)"],
	["\\#!syntax: newline=ignore, space=emit", "Absorb raw newlines, emit spaces"],
	["\\#!syntax: newline=emit, space=ignore", "Emit newlines as Enter, absorb raw spaces"],
	["\\#!syntax: newline=ignore", "Layout config: ignore raw newlines"],
	["\\#!syntax: space=emit", "Layout config: emit raw space keystrokes"],
	["\\#!syntax: newline=emit", "Layout config: emit raw Enter keystrokes"],
	["\\#!syntax: space=ignore", "Layout config: ignore raw spaces and tabs"]
].map(([l, d]) => ({ label: l, insert: l + "\n", desc: d, kind: "syntax" }));

export const ESCAPE_CANDIDATES: InspectorCandidate[] = [
	["\\#", "\\# ", "Single-line comment (or literal hash)"],
	["\\\\", "Literal backslash character"],
	["\\{", "Literal open brace character"],
	["\\}", "Literal close brace character"],
	["\\$", "Literal dollar character"]
].map(([l, a, b]) => ({ label: l, insert: b ? a : l, desc: b || a, kind: "escape" }));

export const MOUSE_BUTTON_CANDIDATES: InspectorCandidate[] = [
	["L", "Left mouse button (primary)"],
	["R", "Right mouse button (context menu)"],
	["M", "Middle mouse button (wheel click)"],
	["B", "Back mouse button (X1 / browser back)"],
	["F", "Forward mouse button (X2 / browser forward)"]
].map(([l, d]) => ({ label: l, insert: l, kind: "btn", desc: d }));

export const KEY_NAME_CANDIDATES: InspectorCandidate[] = [
	["ctrl", "Control modifier (aliases: control, lctrl, rctrl)"],
	["shift", "Shift modifier (alias: lshift)"],
	["alt", "Alt / Option modifier (aliases: opt, option, lalt)"],
	["gui", "GUI / Win / Cmd (aliases: win, cmd, command)"],
	["enter", "Return / Enter key (alias: return)"],
	["return", "Alias for enter"],
	["esc", "Escape key (alias: escape)"],
	["escape", "Alias for esc"],
	["tab", "Tab key"],
	["space", "Spacebar"],
	["bs", "Backspace key (alias: backspace)"],
	["del", "Delete key (alias: delete)"],
	["home", "Home key"],
	["end", "End key"],
	["pgup", "Page Up key (alias: pageup)"],
	["pgdn", "Page Down key (alias: pagedown)"],
	["up", "Arrow Up key"],
	["down", "Arrow Down key"],
	["left", "Arrow Left key"],
	["right", "Arrow Right key"],
	["f1", "Function key F1"],
	["f5", "Function key F5 (Refresh)"],
	["f12", "Function key F12 (DevTools)"],
	["bracketleft", "Left square bracket [ (Usage 0x2F)"],
	["bracketright", "Right square bracket ] (Usage 0x30)"],
	["backslash", "Backslash key \\ (Usage 0x31)"],
	["semicolon", "Semicolon key ; (Usage 0x33)"],
	["quote", "Quote / Apostrophe ' (Usage 0x34)"],
	["apostrophe", "Alias for quote (Usage 0x34)"],
	["grave", "Grave accent / Backtick ` (Usage 0x35)"],
	["slash", "Forward slash / (Usage 0x38)"],
	["comma", "Comma key , (Usage 0x36)"],
	["period", "Period / Dot key . (Usage 0x37)"],
	["minus", "Minus key - (Usage 0x2D)"],
	["equal", "Equal key = (Usage 0x2E)"],
	["ins", "Insert key (Usage 0x49)"],
	["caps", "Caps Lock key (Usage 0x39)"],
	["prtscr", "Print Screen key (Usage 0x46)"]
].map(([k, d]) => ({ label: k, insert: k, kind: "key", desc: d }));

export const NAMESPACE_CANDIDATES: InspectorCandidate[] = [
	["sys", "System control & heap diagnostic namespace"],
	["net", "Network Protocol v1 broadcast messaging"],
	["img", "Vision & pixel processing telemetry"]
].map(([l, d]) => ({ label: l, insert: l, kind: "ns", desc: d }));

export const EXT_FUNCTION_CANDIDATES: InspectorCandidate[] = [
	["sys.free", "sys.free", "Query device available heap RAM in bytes -> $ret"],
	["net.send", "net.send}{sync}", "Broadcast message tag to companion hosts"],
	["img.snap", "img.snap}{0, 0, 100, 100}", "Capture screen rectangle -> snapshot Handle in $ret"],
	["img.at", "img.at}{$s}{0, 0}", "Sample 24-bit RGB pixel color from snapshot"],
	["img.diff", "img.diff}{$a}{$b}{16}", "Compare snapshots and count differing pixels"],
	["img.watch", "img.watch}{ready}{400, 200}{50ms, 10s}", "Continuous telemetry stream pushing to $slot.img.<name>.val"],
	["img.unwatch", "img.unwatch}{ready}", "Cancel active telemetry stream subscription"]
].map(([l, i, d]) => ({ label: l, insert: i, desc: d, kind: "cmd" }));

export const CALL_ARG_COUNTS: Record<string, number> = {
	"sys.free": 0,
	"sys.const": 1,
	"net.send": 1,
	"img.snap": 1,
	"img.at": 2,
	"img.diff": 3,
	"img.watch": 3,
	"img.unwatch": 1
};

export const PREDICATE_LOGIC_CANDIDATES: InspectorCandidate[] = [
	["\\all", "\\all{ {$x, >=, 0}, {$x, <=, 100} }", "Conjunction AND logic block"],
	["\\any", "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", "Disjunction OR logic block"]
].map(([l, i, d]) => ({ label: l, insert: i, desc: d, kind: "cmd" }));

export const PREDICATE_OP_CANDIDATES: InspectorCandidate[] = [
	["=", "Equal comparison operator"],
	["!=", "Not equal comparison operator"],
	["<", "Less than comparison operator"],
	["<=", "Less than or equal operator"],
	[">", "Greater than comparison operator"],
	[">=", "Greater than or equal operator"]
].map(([op, d]) => ({ label: op, insert: op, kind: "op", desc: d }));

// =========================================================================
// 3. DSL Structural Multi-Block Signatures & Lone Key Sets
// =========================================================================
const sig = (cmd: string, rules: [string, "brace" | "bracket", boolean][]): MultiBlockSignature => ({
	cmd, rules: rules.map(([name, kind, required]) => ({ name, kind, required }))
});

export const MULTI_BLOCK_SIGNATURES: Record<string, MultiBlockSignature> = {
	"\\if": sig("\\if", [["predicate", "brace", true], ["then", "brace", true], ["else", "brace", false]]),
	"\\rep": sig("\\rep", [["count", "brace", true], ["body", "brace", true]]),
	"\\guarded": sig("\\guarded", [["throttle", "bracket", false], ["predicate", "brace", true], ["body", "brace", true], ["abort", "brace", false]]),
	"\\def": sig("\\def", [["name", "brace", true], ["argc", "bracket", false], ["body", "brace", true]]),
	"\\set": sig("\\set", [["name", "brace", true], ["value", "brace", true]]),
	"\\add": sig("\\add", [["name", "brace", true], ["value", "brace", true]]),
	"\\sub": sig("\\sub", [["name", "brace", true], ["value", "brace", true]]),
	"\\pixelrect": sig("\\pixelrect", [["name", "brace", true], ["rows", "brace", true]])
};

export const LONE_KEY_NAMES = new Set(
	("enter return esc escape tab space bs backspace del delete home end pgup pageup pgdn pagedown up down left right " +
	"ctrl control lctrl rctrl shift lshift rshift alt opt option lalt ralt gui win cmd command rgui " +
	"caps capslock numlock scrolllock prtscr printscreen pause menu application ins insert minus equal equals plus " +
	"bracketleft bracketright backslash semicolon quote apostrophe grave slash comma period dot " +
	"mute volup voldn voldown media_next media_prev media_play media_pause media_playpause media_stop brightup brightdn sys_sleep " +
	Array.from({ length: 24 }, (_, i) => `f${i + 1}`).join(" ")).split(" ")
);
