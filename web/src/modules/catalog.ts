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

export interface ManualParam {
	name: string;
	type: string;
	range?: string;
	desc: string;
	optional?: boolean;
}

export interface ManualItem {
	name: string;
	syntax?: string;
	desc: string;
	snippet: string;
	aliases?: string[];
	usageId?: string;
	params?: ManualParam[];
	fields?: FormField[];
	template?: string;
	returns?: string;
	multiline?: boolean;
	keywords?: string;
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
		params?: [string, string, string?, string?, boolean?][];
		fields?: [string, "select" | "text", string, string[]?, string?, string?][];
		template?: string;
		returns?: string;
		multiline?: boolean;
		keywords?: string;
	}
): ManualItem {
	return {
		name,
		syntax,
		desc,
		snippet,
		aliases: opts?.aliases,
		usageId: opts?.usageId,
		params: opts?.params?.map(([name, type, range, desc, optional]) => ({
			name,
			type,
			range,
			desc: desc || "",
			optional
		})),
		fields: opts?.fields?.map(([name, type, def, options, placeholder, width]) => ({
			name,
			type,
			default: def,
			options,
			placeholder,
			width
		})),
		template: opts?.template,
		returns: opts?.returns,
		multiline: opts?.multiline,
		keywords: opts?.keywords
	};
}



// =========================================================================
// 1. Interactive Manual Drawer Category Data
// =========================================================================
export const MANUAL_DATA: ManualCategory[] = [
	{
		name: "Preamble & Syntax Layout",
		badge: "Preamble",
		desc: "Layout directives controlling raw whitespace, newlines, and indentation absorption.",
		items: [
			item("Free-form Layout", "\\#!syntax: free", "Absorbs raw newlines and spaces. Whitespace only serves to delimit tokens.", "\\#!syntax: free\n", {
				keywords: "syntax free typewriter layout preamble whitespace ignore indent"
			}),
			item("Typewriter Layout", "\\#!syntax: typewriter", "Raw Enter emits Return keystrokes, raw spaces emit Space keystrokes verbatim.", "\\#!syntax: typewriter\n", {
				keywords: "syntax typewriter layout raw enter space emit"
			}),
			item("Custom Whitespace Policy", "\\#!syntax: newline=ignore, space=emit", "Fine-grained control over raw newline and space treatment.", "\\#!syntax: newline=ignore, space=emit\n", {
				keywords: "syntax newline ignore space emit fine layout config"
			})
		]
	},
	{
		name: "Special Escapes & Text Blocks",
		badge: "Syntax",
		desc: "Reserved character escapes, comments, and verbatim keystroke payloads.",
		items: [
			item("Literal Backslash (\\\\)", "\\\\", "Emits a literal backslash keystroke.", "\\\\", { keywords: "escape backslash slash" }),
			item("Literal Braces (\\{, \\})", "\\{ or \\}", "Emits literal open or close curly braces.", "\\{", { keywords: "escape brace curly left right" }),
			item("Literal Dollar (\\$)", "\\$", "Emits literal dollar sign without triggering variable interpolation.", "\\$", { keywords: "escape dollar reference currency" }),
			item("Single-line Comment (\\#)", "\\# <text>", "Comment line ignored by compiler. Extends to end of physical line.", "\\# comment\n", {
				fields: [["text", "text", "250", undefined, "comment text...", undefined]],
				template: "\\# ${text}\n",
				keywords: "comment remark note hash"
			}),
			item("Line Continuation (\\)", "\\ at line end", "Splits long macro statements across lines without emitting newlines.", "\\\n", {
				keywords: "continuation newline split multiline"
			}),
			item("Verbatim Text Block (\\text)", "\\text{}<payload>\\endtext{}", "Types entire block content verbatim as ASCII keystrokes without escaping braces or backslashes.", "\\text{}echo \"Hello World\"\\endtext{}", {
				multiline: true,
				keywords: "text verbatim raw block endtext payload"
			})
		]
	},
	{
		name: "Keyboard: Editing & Navigation",
		badge: "Keys",
		desc: "Standard typing, navigation, and editing keys with US layout HID usage IDs.",
		items: [
			item("Enter / Return", "\\enter", "Enter / Return key tap.", "\\enter", { aliases: ["return"], usageId: "0x28", keywords: "enter return key editing" }),
			item("Escape", "\\esc", "Escape key tap.", "\\esc", { aliases: ["escape"], usageId: "0x29", keywords: "esc escape key cancel" }),
			item("Backspace", "\\bs", "Backspace key tap.", "\\bs", { aliases: ["backspace"], usageId: "0x2A", keywords: "bs backspace erase delete key" }),
			item("Tab", "\\tab", "Tab key tap.", "\\tab", { usageId: "0x2B", keywords: "tab key indent" }),
			item("Spacebar", "\\space", "Spacebar key tap.", "\\space", { usageId: "0x2C", keywords: "space spacebar blank" }),
			item("Forward Delete", "\\del", "Forward Delete key tap.", "\\del", { aliases: ["delete"], usageId: "0x4C", keywords: "del delete forward delete key" }),
			item("Insert", "\\ins", "Insert key tap.", "\\ins", { aliases: ["insert"], usageId: "0x49", keywords: "ins insert key" }),
			item("Home", "\\home", "Home cursor navigation key.", "\\home", { usageId: "0x4A", keywords: "home cursor navigation start" }),
			item("End", "\\end", "End cursor navigation key.", "\\end", { usageId: "0x4D", keywords: "end cursor navigation finish" }),
			item("Page Up", "\\pgup", "Page Up scroll key.", "\\pgup", { aliases: ["pageup"], usageId: "0x4B", keywords: "pgup pageup scroll up" }),
			item("Page Down", "\\pgdn", "Page Down scroll key.", "\\pgdn", { aliases: ["pagedown"], usageId: "0x4E", keywords: "pgdn pagedown scroll down" }),
			item("Arrow Keys", "\\up / \\down / \\left / \\right", "Directional arrow keys (Up=0x52, Down=0x51, Left=0x50, Right=0x4F).", "\\up", {
				fields: [["arrow", "select", "up", ["up", "down", "left", "right"], undefined, undefined]],
				template: "\\${arrow}",
				usageId: "0x4F..0x52",
				keywords: "up down left right arrow direction cursor navigation"
			}),
			item("Equal / Plus", "\\equal / \\plus", "Main keyboard equal key (0x2E) and shifted plus key.", "\\equal", {
				fields: [["key", "select", "equal", ["equal", "plus"], undefined, undefined]],
				template: "\\${key}",
				usageId: "0x2E",
				keywords: "equal plus add assignment"
			}),
			item("Minus / Underscore", "\\minus", "Main keyboard minus/dash key (0x2D).", "\\minus", { usageId: "0x2D", keywords: "minus dash hyphen underscore" }),
			item("Function Keys (F1-F12)", "\\f1..\\f12", "Standard top-row function keys (F1=0x3A .. F12=0x45).", "\\f1", {
				fields: [["fn", "select", "f1", ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"], undefined, undefined]],
				template: "\\${fn}",
				usageId: "0x3A..0x45",
				keywords: "f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 fn function"
			}),
			item("Extended Function Keys (F13-F24)", "\\f13..\\f24", "Extended productivity function keys (F13=0x68 .. F24=0x73).", "\\f13", {
				fields: [["fn", "select", "f13", ["f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20", "f21", "f22", "f23", "f24"], undefined, undefined]],
				template: "\\${fn}",
				usageId: "0x68..0x73",
				keywords: "f13 f14 f15 f16 f17 f18 f19 f20 f21 f22 f23 f24 extended"
			}),
			item("Keypad Digits (0-9)", "\\kp_0..\\kp_9", "Numeric keypad digits (kp_0=0x62 .. kp_9=0x59).", "\\kp_0", {
				fields: [["digit", "select", "kp_0", ["kp_0", "kp_1", "kp_2", "kp_3", "kp_4", "kp_5", "kp_6", "kp_7", "kp_8", "kp_9"], undefined, undefined]],
				template: "\\${digit}",
				usageId: "0x59..0x62",
				keywords: "kp_0 kp_1 kp_2 kp_3 kp_4 kp_5 kp_6 kp_7 kp_8 kp_9 keypad numpad"
			}),
			item("Keypad Operators", "\\kp_enter / \\kp_plus / ...", "Keypad arithmetic and control keys.", "\\kp_enter", {
				fields: [["op", "select", "kp_enter", ["kp_enter", "kp_plus", "kp_minus", "kp_multiply", "kp_divide", "kp_dot", "kp_equal"], undefined, undefined]],
				template: "\\${op}",
				usageId: "0x54..0x67",
				keywords: "kp_enter kp_plus kp_minus kp_multiply keypad numpad"
			}),
			item("Consumer Audio & Media Keys", "\\volup / \\voldn / \\mute / ...", "USB Consumer Control page 0x0C media keys.", "\\volup", {
				fields: [["cmd", "select", "volup", ["volup", "voldn", "mute", "media_play", "media_pause", "media_playpause", "media_next", "media_prev", "media_stop"], undefined, undefined]],
				template: "\\${cmd}",
				usageId: "0x0C:0xE9..0xCD",
				keywords: "volup voldn mute media play pause media_play media_pause sound volume"
			})
		]
	},
	{
		name: "Modifiers & Key Combinations",
		badge: "Modifiers",
		desc: "Modifier keys, chording combinations with +, and persistent press/release holds.",
		items: [
			item("Control Modifier", "\\ctrl / \\lctrl / \\rctrl", "Control key tap or combination prefix.", "\\ctrl", { aliases: ["lctrl", "rctrl"], keywords: "ctrl control lctrl rctrl modifier" }),
			item("Shift Modifier", "\\shift / \\lshift / \\rshift", "Shift key tap or combination prefix.", "\\shift", { aliases: ["lshift", "rshift"], keywords: "shift lshift rshift modifier" }),
			item("Alt / Option Modifier", "\\alt / \\lalt / \\ralt", "Alt / Option key tap or combination prefix.", "\\alt", { aliases: ["lalt", "ralt", "opt", "ropt"], keywords: "alt opt option modifier" }),
			item("GUI / Command / Windows Modifier", "\\gui / \\cmd / \\win", "OS command/super key tap or combination prefix.", "\\cmd", { aliases: ["gui", "win", "lgui", "rgui", "lcmd", "rcmd"], keywords: "gui win cmd command modifier" }),
			item("Keyboard Shortcut Combinations", "\\ctrl+c / \\cmd+space / ...", "Atomic chords pressed and released together in order.", "\\ctrl+c", {
				fields: [["combo", "select", "ctrl+c", ["ctrl+c", "ctrl+v", "ctrl+z", "ctrl+a", "ctrl+s", "cmd+space", "alt+tab", "ctrl+shift+t"], undefined, undefined]],
				template: "\\${combo}",
				keywords: "combo shortcut chord chain copy paste"
			}),
			item("Key Hold Down (\\kdown)", "\\kdown{<key>}", "Presses and holds key down without releasing. Supports modifiers and chords.", "\\kdown{ctrl}", {
				fields: [["key", "text", "ctrl", undefined, "key name...", "80px"]],
				template: "\\kdown{${key}}",
				keywords: "kdown key hold press down modifier assertion"
			}),
			item("Key Release Up (\\kup)", "\\kup{[key]}", "Releases held key. Empty braces \\kup{} safely releases all keys currently held.", "\\kup{ctrl}", {
				fields: [["key", "text", "ctrl", undefined, "empty for all", "80px"]],
				template: "\\kup{${key}}",
				keywords: "kup key release up modifier safety reset"
			})
		]
	},
	{
		name: "Mouse Actions & Pointer Movement",
		badge: "Mouse",
		desc: "Mouse clicks, multi-click repeats, drag-holds, and relative cursor displacement.",
		items: [
			item("Click Mouse Button (\\click)", "\\click{<btn> [, <count>]}", "Clicks mouse button L, R, M, B (back/X1), or F (forward/X2). Optional count.", "\\click{L}", {
				fields: [
					["btn", "select", "L", ["L", "R", "M", "B", "F"], undefined, "50px"],
					["count", "text", "1", undefined, "count", "45px"]
				],
				template: "\\click{${btn}}",
				keywords: "click mouse button l r m b f left right middle double"
			}),
			item("Relative Mouse Move (\\move)", "\\move{<dx>, <dy>}", "Displaces mouse pointer by signed dx, dy offsets (-32768..32767).", "\\move{100, -50}", {
				fields: [
					["dx", "text", "100", undefined, "dx", "55px"],
					["dy", "text", "-50", undefined, "dy", "55px"]
				],
				template: "\\move{${dx}, ${dy}}",
				keywords: "move mouse cursor relative dx dy pointer displacement"
			}),
			item("Mouse Button Down (\\mdown)", "\\mdown{<btn>}", "Presses and holds mouse button down for dragging.", "\\mdown{L}", {
				fields: [["btn", "select", "L", ["L", "R", "M", "B", "F"], undefined, "50px"]],
				template: "\\mdown{${btn}}",
				keywords: "mdown mouse down hold drag press"
			}),
			item("Mouse Button Up (\\mup)", "\\mup{<btn>}", "Releases held mouse button to finish drag or drop.", "\\mup{L}", {
				fields: [["btn", "select", "L", ["L", "R", "M", "B", "F"], undefined, "50px"]],
				template: "\\mup{${btn}}",
				keywords: "mup mouse up release drop"
			})
		]
	},
	{
		name: "Timing & Action Pacing",
		badge: "Timing",
		desc: "Explicit pause delays and global inter-action keystroke pacing intervals.",
		items: [
			item("Sleep / Delay (\\delay)", "\\delay{<duration>}", "Pauses macro execution for specified duration (e.g. 100ms, 2s).", "\\delay{100ms}", {
				fields: [["duration", "text", "100ms", undefined, "e.g. 50ms, 1s", "90px"]],
				template: "\\delay{${duration}}",
				keywords: "delay sleep pause wait milliseconds ms seconds duration"
			}),
			item("Action Pacing (\\pace)", "\\pace{<duration>}", "Sets global minimum delay injected between subsequent keystrokes and clicks.", "\\pace{20ms}", {
				fields: [["pacing", "text", "20ms", undefined, "e.g. 10ms, 50ms", "90px"]],
				template: "\\pace{${pacing}}",
				keywords: "pace speed throttle rate interval delay auto timing"
			})
		]
	},
	{
		name: "Variables, Registers & Math",
		badge: "Variables",
		desc: "32 signed i32 registers, in-place arithmetic mutations, and decimal typing.",
		items: [
			item("Assign Variable (\\set)", "\\set{<var>}{<val>}", "Allocates general register and assigns signed 32-bit integer or reference.", "\\set{x}{100}", {
				fields: [
					["var", "text", "x", undefined, "var name", "60px"],
					["val", "text", "100", undefined, "value", "70px"]
				],
				template: "\\set{${var}}{${val}}",
				keywords: "set var variable assign store register i32 integer state"
			}),
			item("Add to Variable (\\add)", "\\add{<var>}{<val>}", "In-place addition to general register.", "\\add{x}{1}", {
				fields: [
					["var", "text", "x", undefined, "var name", "60px"],
					["val", "text", "1", undefined, "increment", "70px"]
				],
				template: "\\add{${var}}{${val}}",
				keywords: "add increment plus sum variable math arithmetic"
			}),
			item("Subtract from Variable (\\sub)", "\\sub{<var>}{<val>}", "In-place subtraction from general register.", "\\sub{x}{1}", {
				fields: [
					["var", "text", "x", undefined, "var name", "60px"],
					["val", "text", "1", undefined, "decrement", "70px"]
				],
				template: "\\sub{${var}}{${val}}",
				keywords: "sub decrement minus difference variable math arithmetic"
			}),
			item("Render Number as Keystrokes (\\typenum)", "\\typenum{<var_ref>}", "Converts integer in register to decimal ASCII and types it as keystrokes.", "\\typenum{$x}", {
				fields: [["var", "text", "$x", undefined, "variable", "70px"]],
				template: "\\typenum{${var}}",
				keywords: "typenum type print digits number format decimal output"
			}),
			item("System Registers ($ret, $err, $timeout)", "$ret / $err / $timeout", "Read-only system registers written by calls and wait statements.", "$ret", {
				keywords: "ret err timeout return error status register system flag"
			})
		]
	},
	{
		name: "Control Flow & Guarded Execution",
		badge: "Control",
		desc: "Relational comparisons, compound logic blocks, and invariant-protected execution.",
		items: [
			item("Conditional Branch (\\if)", "\\if{<predicate>}{<then>}[{<else>}]", "Evaluates predicate comparison triplet or logic block, branching accordingly.", "\\if{ {$x, >=, 0} }{\n  \\enter\n}\n", {
				fields: [["cond", "text", "{$x, >=, 0}", undefined, "predicate condition", "120px"]],
				template: "\\if{ ${cond} }{\n  \\enter\n}\n",
				multiline: true,
				keywords: "if branch condition compare equal relational greater less else"
			}),
			item("Conjunction Logic Block (\\all)", "\\all{ {p1}, {p2}, ... }", "Logical AND block: passes only if all inner triplets evaluate to true.", "\\all{ {$x, >=, 0}, {$x, <=, 100} }", {
				keywords: "if all and conjunction logic short-circuit multiple"
			}),
			item("Disjunction Logic Block (\\any)", "\\any{ {p1}, {p2}, ... }", "Logical OR block: passes if any inner triplet evaluates to true.", "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", {
				keywords: "if any or disjunction logic short-circuit error timeout"
			}),
			item("Guarded Execution Block (\\guarded)", "\\guarded{<invariant>}{<body>}[{<abort>]", "Monitors invariant before every action. On violation, aborts and releases all keys.", "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n  \\delay{50ms}\n}{\n  \\esc\n}\n", {
				multiline: true,
				keywords: "guarded invariant safe safety abort protect release exception rollback"
			}),
			item("Guarded with Throttle Interval", "\\guarded[<throttle>]{<invariant>}{<body>}[{<abort>]", "Guard with explicit throttle interval to prevent high CPU or network inspection overhead.", "\\guarded[50ms]{ {$err, =, 0} }{\n  \\click{L}\n}\n", {
				fields: [["throttle", "text", "50ms", undefined, "throttle ms", "60px"]],
				template: "\\guarded[${throttle}]{ {$err, =, 0} }{\n  \\click{L}\n}\n",
				multiline: true,
				keywords: "guarded throttle interval period guard rate rate-limit"
			})
		]
	},
	{
		name: "Loops & User Macros",
		badge: "Loops",
		desc: "Bounded repeat counting loops and compile-time user macros.",
		items: [
			item("Repeat Counting Loop (\\rep)", "\\rep{<count>}{<body>}", "Bounded counting loop repeating body 0..65535 times. Zero stack growth.", "\\rep{10}{\n  \\click{L}\n  \\delay{50ms}\n}\n", {
				fields: [["count", "text", "10", undefined, "count", "50px"]],
				template: "\\rep{${count}}{\n  \\click{L}\n  \\delay{50ms}\n}\n",
				multiline: true,
				keywords: "rep repeat loop count times iteration bounded cycle"
			}),
			item("Define Macro (0 arguments)", "\\def{<name>}[0]{<body>}", "Defines reusable compile-time macro expanded inline at point of use.", "\\def{my_macro}[0]{\\enter}\n", {
				fields: [["name", "text", "my_macro", undefined, "macro name", "90px"]],
				template: "\\def{${name}}[0]{\\enter}\n",
				multiline: true,
				keywords: "def macro function inline define expand template zero"
			}),
			item("Define Macro with Parameters", "\\def{<name>}[N]{<body>}", "Defines parameterized compile-time macro with textual substitution #1..#9.", "\\def{press_key}[1]{\\kdown{#1}\\delay{20ms}\\kup{#1}}\n", {
				fields: [
					["name", "text", "press_key", undefined, "name", "85px"],
					["argc", "text", "1", undefined, "argc", "40px"]
				],
				template: "\\def{${name}}[${argc}]{\\kdown{#1}\\delay{20ms}\\kup{#1}}\n",
				multiline: true,
				keywords: "def macro parameter args substitution expand user"
			})
		]
	},
	{
		name: "Signals, Slots & Extensions",
		badge: "Runtime",
		desc: "Asynchronous signal arrival latches, namespaces, and runtime extension functions.",
		items: [
			item("Signal Slot References ($slot)", "$slot.<sig>.<field>", "Read-only receptor slot holding latest value broadcast by signal.", "$slot.net.msg.val", {
				keywords: "slot signal field receptor state event projection async"
			}),
			item("Wait for Signal (\\wait)", "\\wait[<timeout>]{<slot>}", "Suspends execution until signal arrives or timeout expires. Sets $timeout=0/1.", "\\wait[5s]{ $slot.net.msg.val }", {
				fields: [
					["timeout", "text", "5s", undefined, "timeout", "55px"],
					["slot", "text", "$slot.net.msg.val", undefined, "slot receptor", "130px"]
				],
				template: "\\wait[${timeout}]{ ${slot} }",
				keywords: "wait timeout suspend event signal arrival latch async poll nonblocking"
			}),
			item("Activate Namespace (\\use)", "\\use{<ns>}", "Declares dependency on runtime extension namespace (sys, net, img).", "\\use{sys}\n", {
				fields: [["ns", "select", "sys", ["sys", "net", "img"], undefined, "65px"]],
				template: "\\use{${ns}}\n",
				keywords: "use namespace activate import extension module"
			}),
			item("Call Extension (\\call)", "\\call{<target>}{<args>...}", "Invokes registered extension function; writes return code to $ret and error to $err.", "\\call{sys.free}", {
				fields: [["target", "select", "sys.free", ["sys.free", "net.send", "img.snap", "img.at", "img.diff", "img.watch", "img.unwatch"], undefined, "95px"]],
				template: "\\call{${target}}",
				keywords: "call ext extension function invoke runtime"
			}),
			item("Query Free Heap (sys.free)", "\\call{sys.free}", "Queries device GC free heap bytes.", "\\call{sys.free}", {
				returns: "$ret (free bytes)",
				keywords: "use sys call free heap memory ram system diagnostic"
			}),
			item("Send Network Message (net.send)", "\\call{net.send}{<msg>}", "Broadcasts framed message envelope to all connected TCP clients.", "\\call{net.send}{hello}", {
				keywords: "use net call send broadcast network message host sync"
			}),
			item("Capture Framebuffer Snapshot (img.snap)", "\\call{img.snap}{<x>, <y>, <w>, <h>}", "Captures rectangular region from stream into snap handle.", "\\call{img.snap}{0, 0, 100, 100}", {
				returns: "$ret (snap handle)",
				keywords: "use img call snap snapshot capture screen framebuffer handle vision"
			}),
			item("Sample Pixel Color (img.at)", "\\call{img.at}{$snap}{<x>, <y>}", "Inspects packed 24-bit RGB color at specified point coord within snap handle.", "\\call{img.at}{$snap}{50, 50}", {
				returns: "$ret (0xRRGGBB)",
				keywords: "call img at pixel color rgb sample point coord inspection"
			}),
			item("Compare Snapshots (img.diff)", "\\call{img.diff}{$a, $b, <tol>}", "Computes mismatching pixel count between two snap handles within per-channel tolerance.", "\\call{img.diff}{$a, $b, 10}", {
				returns: "$ret (delta count)",
				keywords: "call img diff compare difference tolerance match count delta"
			}),
			item("Watch Stream Telemetry (img.watch)", "\\call{img.watch}{<name>, <pt>, <span_t>}", "Registers background telemetry observer emitting arrival signals.", "\\call{img.watch}{hp, {100, 50}, {50ms, 5s}}", {
				keywords: "call img watch stream monitor observer background predicate telemetry"
			}),
			item("Stop Watcher (img.unwatch)", "\\call{img.unwatch}{<name>}", "Cancels active stream observer.", "\\call{img.unwatch}{hp}", {
				keywords: "call img unwatch stop cancel unsubscribe stream"
			})
		]
	},
	{
		name: "Literal Constructors",
		badge: "Literals",
		desc: "Compile-time immediate packed 24-bit RGB scalars and pixel matrix literals.",
		items: [
			item("Pack RGB Color (\\rgb)", "\\rgb{<r>, <g>, <b>}", "Packs red, green, blue intensities (0..255) into a 24-bit scalar immediate.", "\\rgb{255, 0, 0}", {
				fields: [
					["r", "text", "255", undefined, "r", "40px"],
					["g", "text", "0", undefined, "g", "40px"],
					["b", "text", "0", undefined, "b", "40px"]
				],
				template: "\\rgb{${r}, ${g}, ${b}}",
				keywords: "rgb color literal red green blue packed 0x immediate hex"
			}),
			item("Pixel Matrix Literal (\\pixelrect)", "\\pixelrect{<dst>}{\\row{...}}", "Materializes a multi-row pixel pattern as a snapshot handle.", "\\pixelrect{pat}{\n  \\row{0xFF0000, 0x00FF00}\n  \\row{0x0000FF, 0xFFFFFF}\n}\n", {
				multiline: true,
				keywords: "pixelrect row rect literal matrix template const handle snapshot"
			})
		]
	},
	{
		name: "Practical Recipes",
		badge: "Recipes",
		desc: "Ready-to-use tested macro automation workflows.",
		items: [
			item("Rapid Mouse Clicker Loop", "Rapid Clicker Recipe", "Clicks left mouse button 50 times with 10ms pacing.", "\\#!syntax: free\n\\pace{10ms}\n\\rep{50}{\n  \\click{L}\n}\n", {
				multiline: true,
				keywords: "recipe rapid clicker repeat loop pace free syntax"
			}),
			item("Guarded Clipboard Copy-Paste", "Guarded Copy-Paste Recipe", "Guarded shortcut execution. If any fault occurs, all keys are safely released before running the abort recovery block.", "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n  \\delay{50ms}\n  \\ctrl+v\n}{\n  \\kup{}\n}\n", {
				multiline: true,
				keywords: "recipe copy paste guarded invariant safe release keys"
			}),
			item("Pixel Color Inspection & Branch", "Pixel Branch Recipe", "Captures screen region, checks pixel green intensity, clicks if verified.", "\\use{img}\n\\set{snap}{\\call{img.snap}{100, 100, 50, 50}}\n\\set{col}{\\call{img.at}{$snap}{25, 25}}\n\\if{ {$col, =, 0x00FF00} }{\n  \\click{L}\n}\n", {
				multiline: true,
				keywords: "recipe pixel color inspection sample green click condition img snap"
			}),
			item("Stream Telemetry Observer Wait", "Stream Watcher Recipe", "Watches point coord and waits for signal update latch.", "\\use{img}\n\\call{img.watch}{btn, {200, 150}, {50ms, 10s}}\n\\wait[10s]{ $slot.img.ready.val }\n", {
				multiline: true,
				keywords: "recipe watch stream observer wait signal slot timeout event"
			}),
			item("Dynamic Keystroke Counter Loop", "Counter Typing Recipe", "Loops and types numbers 1 to 10 as ASCII digits.", "\\set{x}{1}\n\\rep{10}{\n  \\typenum{$x}\n  \\space\n  \\add{x}{1}\n}\n", {
				multiline: true,
				keywords: "recipe counter loop add typenum dynamic numbers"
			})
		]
	}
];

// =========================================================================
// 2. LSP Inspector Candidate Dictionaries (Derived / Canonical)
// =========================================================================
export const COMMAND_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\set", insert: "\\set{x}{100}", kind: "cmd", desc: "Declare & assign variable" },
	{ label: "\\add", insert: "\\add{x}{1}", kind: "cmd", desc: "In-place addition" },
	{ label: "\\sub", insert: "\\sub{x}{1}", kind: "cmd", desc: "In-place subtraction" },
	{ label: "\\click", insert: "\\click{L}", kind: "cmd", desc: "Click mouse button [L/R/M/B/F]" },
	{ label: "\\move", insert: "\\move{100, -50}", kind: "cmd", desc: "Relative mouse movement dx, dy" },
	{ label: "\\mdown", insert: "\\mdown{L}", kind: "cmd", desc: "Press and hold mouse button" },
	{ label: "\\mup", insert: "\\mup{L}", kind: "cmd", desc: "Release held mouse button" },
	{ label: "\\delay", insert: "\\delay{100ms}", kind: "cmd", desc: "Explicit sleep duration" },
	{ label: "\\pace", insert: "\\pace{20ms}", kind: "cmd", desc: "Set VM inter-action pacing" },
	{ label: "\\rep", insert: "\\rep{10}{\n  \\click{L}\n  \\delay{50ms}\n}\n", kind: "cmd", desc: "Bounded repeat loop" },
	{ label: "\\if", insert: "\\if{ {$x, >=, 0} }{\n  \\enter\n}\n", kind: "cmd", desc: "Conditional branch" },
	{ label: "\\all", insert: "\\all{ {$x, >=, 0}, {$x, <=, 100} }", kind: "cmd", desc: "Conjunction AND logic block" },
	{ label: "\\any", insert: "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", kind: "cmd", desc: "Disjunction OR logic block" },
	{ label: "\\guarded", insert: "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n}{\n  \\esc\n}\n", kind: "cmd", desc: "Guarded protected execution block" },
	{ label: "\\wait", insert: "\\wait[5s]{ $slot.net.msg.val }", kind: "cmd", desc: "Wait for signal arrival latch" },
	{ label: "\\def", insert: "\\def{my_macro}[0]{\\enter}\n", kind: "cmd", desc: "Define compile-time text macro" },
	{ label: "\\use", insert: "\\use{sys}\n", kind: "cmd", desc: "Activate runtime namespace" },
	{ label: "\\call", insert: "\\call{sys.free}", kind: "cmd", desc: "Invoke runtime extension function" },
	{ label: "\\typenum", insert: "\\typenum{$x}", kind: "cmd", desc: "Render decimal integer as keystrokes" },
	{ label: "\\text", insert: "\\text{}payload\\endtext{}", kind: "cmd", desc: "Verbatim text keystroke block" },
	{ label: "\\endtext", insert: "\\endtext{}", kind: "cmd", desc: "End verbatim text block" },
	{ label: "\\rgb", insert: "\\rgb{255, 0, 0}", kind: "cmd", desc: "Pack 24-bit RGB scalar literal" },
	{ label: "\\pixelrect", insert: "\\pixelrect{target}{\\row{0xFF0000, 0x00FF00}}", kind: "cmd", desc: "Materialize snapshot pixel matrix" },
	{ label: "\\row", insert: "\\row{0xFF0000, 0x00FF00}", kind: "cmd", desc: "Pixel row color vector" },
	{ label: "\\kdown", insert: "\\kdown{ctrl}", kind: "cmd", desc: "Press and hold key down" },
	{ label: "\\kup", insert: "\\kup{ctrl}", kind: "cmd", desc: "Release key (empty {} for all keys)" },
	{ label: "\\enter", insert: "\\enter", kind: "key", desc: "Return / Enter key (alias: \\return)" },
	{ label: "\\return", insert: "\\return", kind: "key", desc: "Alias for \\enter (Usage 0x28)" },
	{ label: "\\esc", insert: "\\esc", kind: "key", desc: "Escape key (alias: \\escape)" },
	{ label: "\\escape", insert: "\\escape", kind: "key", desc: "Alias for \\esc (Usage 0x29)" },
	{ label: "\\tab", insert: "\\tab", kind: "key", desc: "Tab key (Usage 0x2B)" },
	{ label: "\\space", insert: "\\space", kind: "key", desc: "Spacebar character tap" },
	{ label: "\\bs", insert: "\\bs", kind: "key", desc: "Backspace key (alias: \\backspace)" },
	{ label: "\\backspace", insert: "\\backspace", kind: "key", desc: "Alias for \\bs (Usage 0x2A)" },
	{ label: "\\del", insert: "\\del", kind: "key", desc: "Forward Delete key (alias: \\delete)" },
	{ label: "\\delete", insert: "\\delete", kind: "key", desc: "Alias for \\del (Usage 0x4C)" },
	{ label: "\\ins", insert: "\\ins", kind: "key", desc: "Insert key (alias: \\insert)" },
	{ label: "\\insert", insert: "\\insert", kind: "key", desc: "Alias for \\ins (Usage 0x49)" },
	{ label: "\\home", insert: "\\home", kind: "key", desc: "Home navigation key" },
	{ label: "\\end", insert: "\\end", kind: "key", desc: "End navigation key" },
	{ label: "\\pgup", insert: "\\pgup", kind: "key", desc: "Page Up key (alias: \\pageup)" },
	{ label: "\\pageup", insert: "\\pageup", kind: "key", desc: "Alias for \\pgup (Usage 0x4B)" },
	{ label: "\\pgdn", insert: "\\pgdn", kind: "key", desc: "Page Down key (alias: \\pagedown)" },
	{ label: "\\pagedown", insert: "\\pagedown", kind: "key", desc: "Alias for \\pgdn (Usage 0x4E)" },
	{ label: "\\up", insert: "\\up", kind: "key", desc: "Arrow Up key" },
	{ label: "\\down", insert: "\\down", kind: "key", desc: "Arrow Down key" },
	{ label: "\\left", insert: "\\left", kind: "key", desc: "Arrow Left key" },
	{ label: "\\right", insert: "\\right", kind: "key", desc: "Arrow Right key" },
	{ label: "\\caps", insert: "\\caps", kind: "key", desc: "Caps Lock key (alias: \\capslock)" },
	{ label: "\\capslock", insert: "\\capslock", kind: "key", desc: "Alias for \\caps (Usage 0x39)" },
	{ label: "\\prtscr", insert: "\\prtscr", kind: "key", desc: "Print Screen key (Usage 0x46)" },
	{ label: "\\ctrl+c", insert: "\\ctrl+c", kind: "key", desc: "Copy clipboard shortcut" },
	{ label: "\\ctrl+v", insert: "\\ctrl+v", kind: "key", desc: "Paste clipboard shortcut" },
	{ label: "\\ctrl+z", insert: "\\ctrl+z", kind: "key", desc: "Undo shortcut" },
	{ label: "\\ctrl+a", insert: "\\ctrl+a", kind: "key", desc: "Select All shortcut" },
	{ label: "\\ctrl+s", insert: "\\ctrl+s", kind: "key", desc: "Save document shortcut" },
	{ label: "\\cmd+space", insert: "\\cmd+space", kind: "key", desc: "Spotlight / Launcher shortcut" },
	{ label: "\\alt+tab", insert: "\\alt+tab", kind: "key", desc: "Switch application window" },
	{ label: "\\volup", insert: "\\volup", kind: "key", desc: "Volume increment (Consumer 0x00E9)" },
	{ label: "\\voldn", insert: "\\voldn", kind: "key", desc: "Volume decrement (Consumer 0x00EA)" },
	{ label: "\\mute", insert: "\\mute", kind: "key", desc: "Audio mute toggle (Consumer 0x00E2)" },
	{ label: "\\media_playpause", insert: "\\media_playpause", kind: "key", desc: "Media Play/Pause (Consumer 0x00CD)" },
	{ label: "\\media_play", insert: "\\media_play", kind: "key", desc: "Media Play (Consumer 0x00B0)" },
	{ label: "\\media_pause", insert: "\\media_pause", kind: "key", desc: "Media Pause (Consumer 0x00B1)" },
	{ label: "\\media_next", insert: "\\media_next", kind: "key", desc: "Media Next track (Consumer 0x00B5)" },
	{ label: "\\media_prev", insert: "\\media_prev", kind: "key", desc: "Media Previous track (Consumer 0x00B6)" },
	{ label: "\\media_stop", insert: "\\media_stop", kind: "key", desc: "Media Stop track (Consumer 0x00B7)" },
	{ label: "\\brightup", insert: "\\brightup", kind: "key", desc: "Brightness increment (Consumer 0x006F)" },
	{ label: "\\brightdn", insert: "\\brightdn", kind: "key", desc: "Brightness decrement (Consumer 0x0070)" },
	{ label: "\\sys_sleep", insert: "\\sys_sleep", kind: "key", desc: "System Sleep / Suspend (Consumer 0x0032)" }
];

export const SYNTAX_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\#!syntax: free", insert: "\\#!syntax: free\n", kind: "syntax", desc: "Free-form layout (absorbs raw whitespace)" },
	{ label: "\\#!syntax: typewriter", insert: "\\#!syntax: typewriter\n", kind: "syntax", desc: "Typewriter layout (raw Enter/Space emit)" },
	{ label: "\\#!syntax: newline=ignore, space=emit", insert: "\\#!syntax: newline=ignore, space=emit\n", kind: "syntax", desc: "Absorb raw newlines, emit spaces" },
	{ label: "\\#!syntax: newline=emit, space=ignore", insert: "\\#!syntax: newline=emit, space=ignore\n", kind: "syntax", desc: "Emit newlines as Enter, absorb raw spaces" },
	{ label: "\\#!syntax: newline=ignore", insert: "\\#!syntax: newline=ignore\n", kind: "syntax", desc: "Layout config: ignore raw newlines" },
	{ label: "\\#!syntax: space=emit", insert: "\\#!syntax: space=emit\n", kind: "syntax", desc: "Layout config: emit raw space keystrokes" },
	{ label: "\\#!syntax: newline=emit", insert: "\\#!syntax: newline=emit\n", kind: "syntax", desc: "Layout config: emit raw Enter keystrokes" },
	{ label: "\\#!syntax: space=ignore", insert: "\\#!syntax: space=ignore\n", kind: "syntax", desc: "Layout config: ignore raw spaces and tabs" }
];

export const ESCAPE_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\#", insert: "\\# ", kind: "escape", desc: "Single-line comment (or literal hash)" },
	{ label: "\\\\", insert: "\\\\", kind: "escape", desc: "Literal backslash character" },
	{ label: "\\{", insert: "\\{", kind: "escape", desc: "Literal open brace character" },
	{ label: "\\}", insert: "\\}", kind: "escape", desc: "Literal close brace character" },
	{ label: "\\$", insert: "\\$", kind: "escape", desc: "Literal dollar character" }
];

export const MOUSE_BUTTON_CANDIDATES: InspectorCandidate[] = [
	{ label: "L", insert: "L", kind: "btn", desc: "Left mouse button (primary)" },
	{ label: "R", insert: "R", kind: "btn", desc: "Right mouse button (context menu)" },
	{ label: "M", insert: "M", kind: "btn", desc: "Middle mouse button (wheel click)" },
	{ label: "B", insert: "B", kind: "btn", desc: "Back mouse button (X1 / browser back)" },
	{ label: "F", insert: "F", kind: "btn", desc: "Forward mouse button (X2 / browser forward)" }
];

export const KEY_NAME_CANDIDATES: InspectorCandidate[] = [
	{ label: "ctrl", insert: "ctrl", kind: "key", desc: "Control modifier (aliases: control, lctrl, rctrl)" },
	{ label: "shift", insert: "shift", kind: "key", desc: "Shift modifier (alias: lshift)" },
	{ label: "alt", insert: "alt", kind: "key", desc: "Alt / Option modifier (aliases: opt, option, lalt)" },
	{ label: "gui", insert: "gui", kind: "key", desc: "GUI / Win / Cmd (aliases: win, cmd, command)" },
	{ label: "enter", insert: "enter", kind: "key", desc: "Return / Enter key (alias: return)" },
	{ label: "return", insert: "return", kind: "key", desc: "Alias for enter" },
	{ label: "esc", insert: "esc", kind: "key", desc: "Escape key (alias: escape)" },
	{ label: "escape", insert: "escape", kind: "key", desc: "Alias for esc" },
	{ label: "tab", insert: "tab", kind: "key", desc: "Tab key" },
	{ label: "space", insert: "space", kind: "key", desc: "Spacebar" },
	{ label: "bs", insert: "bs", kind: "key", desc: "Backspace key (alias: backspace)" },
	{ label: "del", insert: "del", kind: "key", desc: "Delete key (alias: delete)" },
	{ label: "home", insert: "home", kind: "key", desc: "Home key" },
	{ label: "end", insert: "end", kind: "key", desc: "End key" },
	{ label: "pgup", insert: "pgup", kind: "key", desc: "Page Up key (alias: pageup)" },
	{ label: "pgdn", insert: "pgdn", kind: "key", desc: "Page Down key (alias: pagedown)" },
	{ label: "up", insert: "up", kind: "key", desc: "Arrow Up key" },
	{ label: "down", insert: "down", kind: "key", desc: "Arrow Down key" },
	{ label: "left", insert: "left", kind: "key", desc: "Arrow Left key" },
	{ label: "right", insert: "right", kind: "key", desc: "Arrow Right key" },
	{ label: "f1", insert: "f1", kind: "key", desc: "Function key F1" },
	{ label: "f5", insert: "f5", kind: "key", desc: "Function key F5 (Refresh)" },
	{ label: "f12", insert: "f12", kind: "key", desc: "Function key F12 (DevTools)" }
];

export const NAMESPACE_CANDIDATES: InspectorCandidate[] = [
	{ label: "sys", insert: "sys", kind: "ns", desc: "System control & heap diagnostic namespace" },
	{ label: "net", insert: "net", kind: "ns", desc: "Network Protocol v1 broadcast messaging" },
	{ label: "img", insert: "img", kind: "ns", desc: "Vision & pixel processing telemetry" }
];

export const EXT_FUNCTION_CANDIDATES: InspectorCandidate[] = [
	{ label: "sys.free", insert: "sys.free", kind: "cmd", desc: "Query device available heap RAM in bytes -> $ret" },
	{ label: "net.send", insert: "net.send}{sync}", kind: "cmd", desc: "Broadcast message tag to companion hosts" },
	{ label: "img.snap", insert: "img.snap}{0, 0, 100, 100}", kind: "cmd", desc: "Capture screen rectangle -> snapshot Handle in $ret" },
	{ label: "img.at", insert: "img.at}{$s}{0, 0}", kind: "cmd", desc: "Sample 24-bit RGB pixel color from snapshot" },
	{ label: "img.diff", insert: "img.diff}{$a}{$b}{16}", kind: "cmd", desc: "Compare snapshots and count differing pixels" },
	{ label: "img.watch", insert: "img.watch}{ready}{400, 200}{50, 10000}", kind: "cmd", desc: "Continuous telemetry stream pushing to $slot.img.<name>.val" },
	{ label: "img.unwatch", insert: "img.unwatch}{ready}", kind: "cmd", desc: "Cancel active telemetry stream subscription" }
];

export const PREDICATE_LOGIC_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\all", insert: "\\all{ {$x, >=, 0}, {$x, <=, 100} }", kind: "cmd", desc: "Conjunction AND logic block" },
	{ label: "\\any", insert: "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", kind: "cmd", desc: "Disjunction OR logic block" }
];

export const PREDICATE_OP_CANDIDATES: InspectorCandidate[] = [
	{ label: "=", insert: "=", kind: "op", desc: "Equal comparison operator" },
	{ label: "!=", insert: "!=", kind: "op", desc: "Not equal comparison operator" },
	{ label: "<", insert: "<", kind: "op", desc: "Less than comparison operator" },
	{ label: "<=", insert: "<=", kind: "op", desc: "Less than or equal operator" },
	{ label: ">", insert: ">", kind: "op", desc: "Greater than comparison operator" },
	{ label: ">=", insert: ">=", kind: "op", desc: "Greater than or equal operator" }
];

// =========================================================================
// 3. DSL Structural Multi-Block Signatures & Lone Key Sets
// =========================================================================
export const MULTI_BLOCK_SIGNATURES: Record<string, MultiBlockSignature> = {
	"\\if": {
		cmd: "\\if",
		rules: [
			{ name: "predicate", kind: "brace", required: true },
			{ name: "then", kind: "brace", required: true },
			{ name: "else", kind: "brace", required: false }
		]
	},
	"\\rep": {
		cmd: "\\rep",
		rules: [
			{ name: "count", kind: "brace", required: true },
			{ name: "body", kind: "brace", required: true }
		]
	},
	"\\guarded": {
		cmd: "\\guarded",
		rules: [
			{ name: "throttle", kind: "bracket", required: false },
			{ name: "predicate", kind: "brace", required: true },
			{ name: "body", kind: "brace", required: true },
			{ name: "abort", kind: "brace", required: false }
		]
	},
	"\\def": {
		cmd: "\\def",
		rules: [
			{ name: "name", kind: "brace", required: true },
			{ name: "argc", kind: "bracket", required: false },
			{ name: "body", kind: "brace", required: true }
		]
	},
	"\\set": {
		cmd: "\\set",
		rules: [
			{ name: "name", kind: "brace", required: true },
			{ name: "value", kind: "brace", required: true }
		]
	},
	"\\add": {
		cmd: "\\add",
		rules: [
			{ name: "name", kind: "brace", required: true },
			{ name: "value", kind: "brace", required: true }
		]
	},
	"\\sub": {
		cmd: "\\sub",
		rules: [
			{ name: "name", kind: "brace", required: true },
			{ name: "value", kind: "brace", required: true }
		]
	},
	"\\pixelrect": {
		cmd: "\\pixelrect",
		rules: [
			{ name: "name", kind: "brace", required: true },
			{ name: "rows", kind: "brace", required: true }
		]
	}
};

export const LONE_KEY_NAMES = new Set([
	"enter", "return", "esc", "escape", "tab", "space", "bs", "backspace", "del", "delete",
	"home", "end", "pgup", "pageup", "pgdn", "pagedown", "up", "down", "left", "right",
	"f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
	"f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20", "f21", "f22", "f23", "f24",
	"ctrl", "control", "lctrl", "rctrl", "shift", "lshift", "rshift", "alt", "opt", "option", "lalt", "ralt",
	"gui", "win", "cmd", "command", "rgui",
	"capslock", "numlock", "scrolllock", "printscreen", "pause", "menu", "application", "insert",
	"minus", "equals",
	"mute", "volup", "voldown", "media_next", "media_prev", "media_play", "media_pause", "media_stop"
]);
