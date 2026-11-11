export interface ManualParam {
	name: string;
	type: string;
	range?: string;
	desc: string;
	optional?: boolean;
}

export interface FormField {
	name: string;
	type: "select" | "text";
	options?: string[];
	default: string;
	placeholder?: string;
	width?: string;
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

export const MANUAL_DATA: ManualCategory[] = [
	{
		name: "Preamble & Layout Directives",
		badge: "Layout",
		desc: "Leading directives controlling whitespace parsing mode (typewriter vs free)",
		items: [
			{
				name: "\\#!syntax: <preset>",
				syntax: "\\#!syntax: <preset>",
				desc: "Preset layout mode controlling unescaped whitespace: free absorbs newlines and indentation; typewriter (default) emits Enter/Space keystrokes.",
				snippet: "\\#!syntax: free\n",
				params: [
					{ name: "preset", type: "free | typewriter", desc: "Preset layout mode: free absorbs unescaped whitespace; typewriter emits Enter/Space" }
				],
				fields: [
					{ name: "preset", type: "select", options: ["free", "typewriter"], default: "free" }
				],
				template: "\\#!syntax: ${preset}\n",
				keywords: "syntax free typewriter layout preamble whitespace ignore indent"
			},
			{
				name: "\\#!syntax: newline=<nl>, space=<sp>",
				syntax: "\\#!syntax: newline=<ignore|emit>, space=<ignore|emit>",
				desc: "Fine-grained layout configuration overriding specific whitespace behavior. Directives or keys can be set individually or combined.",
				snippet: "\\#!syntax: newline=ignore, space=emit\n",
				params: [
					{ name: "newline", type: "ignore | emit", desc: "Whether unescaped newlines are absorbed or emit Enter", optional: true },
					{ name: "space", type: "ignore | emit", desc: "Whether unescaped spaces are absorbed or emit Space", optional: true }
				],
				fields: [
					{ name: "newline", type: "select", options: ["ignore", "emit"], default: "ignore" },
					{ name: "space", type: "select", options: ["emit", "ignore"], default: "emit" }
				],
				template: "\\#!syntax: newline=${newline}, space=${space}\n",
				keywords: "syntax newline ignore space emit fine layout config"
			}
		]
	},
	{
		name: "Literal Text, Escapes & Comments",
		badge: "Text",
		desc: "Special character escaping, single-line remarks and verbatim text payload blocks",
		items: [
			{
				name: "\\\\",
				syntax: "\\\\",
				desc: "Escaped backslash (emits literal \\ character).",
				snippet: "\\\\",
				keywords: "escape backslash slash"
			},
			{
				name: "\\{ / \\}",
				syntax: "\\{ / \\}",
				desc: "Literal curly braces. \\{ escapes left brace; \\} escapes block terminator right brace.",
				snippet: "\\{",
				fields: [
					{ name: "brace", type: "select", options: ["{", "}"], default: "{" }
				],
				template: "\\${brace}",
				keywords: "escape brace curly left right"
			},
			{
				name: "\\$",
				syntax: "\\$",
				desc: "Literal dollar sign (avoids variable reference interpolation in value positions).",
				snippet: "\\$",
				keywords: "escape dollar reference currency"
			},
			{
				name: "\\# comment",
				syntax: "\\# <comment>",
				desc: "Single-line comment. Consumes through line ending; emits zero instructions.",
				snippet: "\\# comment\n",
				fields: [
					{ name: "comment", type: "text", default: "comment", placeholder: "comment text" }
				],
				template: "\\# ${comment}\n",
				keywords: "comment remark note hash"
			},
			{
				name: "\\<newline>",
				syntax: "\\<newline>",
				desc: "Line continuation: backslash immediately followed by newline absorbs the newline.",
				snippet: "\\\n",
				keywords: "continuation newline split multiline"
			},
			{
				name: "Verbatim Text (\\text{}\\endtext{})",
				syntax: "\\text{}<payload>\\endtext{}",
				desc: "Verbatim text block. Payload sits strictly between mandatory empty delimiter braces \\text{} and \\endtext{}. Emits exact keystrokes regardless of active layout mode; CRLF/LF emits Enter.",
				snippet: "\\text{}echo \"Hello World\"\\endtext{}",
				params: [
					{ name: "payload", type: "ASCII text", desc: "Verbatim characters emitted sequentially via CHAR/KEY" }
				],
				fields: [
					{ name: "payload", type: "text", default: 'echo "Hello World"', placeholder: "verbatim text" }
				],
				template: "\\text{}${payload}\\endtext{}",
				keywords: "text verbatim raw block endtext payload"
			}
		]
	},
	{
		name: "Keyboard: Editing & Navigation",
		badge: "Keys",
		desc: "Editing, navigation and cursor keys with full alias and Usage ID mapping",
		items: [
			{
				name: "Enter / Return",
				syntax: "\\enter",
				desc: "Return / Enter key tap.",
				snippet: "\\enter",
				aliases: ["return"],
				usageId: "0x28",
				keywords: "enter return key editing"
			},
			{
				name: "Escape",
				syntax: "\\esc",
				desc: "Escape key tap.",
				snippet: "\\esc",
				aliases: ["escape"],
				usageId: "0x29",
				keywords: "esc escape key cancel"
			},
			{
				name: "Backspace",
				syntax: "\\bs",
				desc: "Backspace key tap.",
				snippet: "\\bs",
				aliases: ["backspace"],
				usageId: "0x2A",
				keywords: "bs backspace erase delete key"
			},
			{
				name: "Tab",
				syntax: "\\tab",
				desc: "Horizontal Tab key tap.",
				snippet: "\\tab",
				usageId: "0x2B",
				keywords: "tab key indent"
			},
			{
				name: "Spacebar",
				syntax: "\\space",
				desc: "Spacebar key tap.",
				snippet: "\\space",
				usageId: "0x2C",
				keywords: "space spacebar blank"
			},
			{
				name: "Forward Delete",
				syntax: "\\del",
				desc: "Forward Delete key tap.",
				snippet: "\\del",
				aliases: ["delete"],
				usageId: "0x4C",
				keywords: "del delete forward delete key"
			},
			{
				name: "Insert",
				syntax: "\\ins",
				desc: "Insert key tap.",
				snippet: "\\ins",
				aliases: ["insert"],
				usageId: "0x49",
				keywords: "ins insert key"
			},
			{
				name: "Home",
				syntax: "\\home",
				desc: "Home navigation key tap.",
				snippet: "\\home",
				usageId: "0x4A",
				keywords: "home cursor navigation start"
			},
			{
				name: "End",
				syntax: "\\end",
				desc: "End navigation key tap.",
				snippet: "\\end",
				usageId: "0x4D",
				keywords: "end cursor navigation finish"
			},
			{
				name: "Page Up",
				syntax: "\\pgup",
				desc: "Page Up navigation key tap.",
				snippet: "\\pgup",
				aliases: ["pageup"],
				usageId: "0x4B",
				keywords: "pgup pageup scroll up"
			},
			{
				name: "Page Down",
				syntax: "\\pgdn",
				desc: "Page Down navigation key tap.",
				snippet: "\\pgdn",
				aliases: ["pagedown"],
				usageId: "0x4E",
				keywords: "pgdn pagedown scroll down"
			},
			{
				name: "Arrow Keys",
				syntax: "\\up / \\down / \\left / \\right",
				desc: "Directional arrow keys (Up=0x52, Down=0x51, Left=0x50, Right=0x4F).",
				snippet: "\\up",
				fields: [
					{ name: "arrow", type: "select", options: ["up", "down", "left", "right"], default: "up" }
				],
				template: "\\${arrow}",
				usageId: "0x4F..0x52",
				keywords: "up down left right arrow direction cursor navigation"
			},
			{
				name: "Minus / Underscore",
				syntax: "\\minus",
				desc: "Main keyboard minus key (Usage ID 0x2D).",
				snippet: "\\minus",
				usageId: "0x2D",
				keywords: "minus dash hyphen underscore"
			},
			{
				name: "Equal / Plus",
				syntax: "\\equal / \\plus",
				desc: "Main keyboard equal key (0x2E) and shifted plus key.",
				snippet: "\\equal",
				fields: [
					{ name: "key", type: "select", options: ["equal", "plus"], default: "equal" }
				],
				template: "\\${key}",
				usageId: "0x2E",
				keywords: "equal plus add assignment"
			}
		]
	},
	{
		name: "Keyboard: Function, System & Keypad",
		badge: "System",
		desc: "F1-F24 function keys, lock keys, system control and numeric keypad",
		items: [
			{
				name: "Function Keys (F1-F12)",
				syntax: "\\f1 .. \\f12",
				desc: "Standard function keys F1 through F12 (Usages 0x3A..0x45).",
				snippet: "\\f5",
				fields: [
					{ name: "fkey", type: "select", options: ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"], default: "f5" }
				],
				template: "\\${fkey}",
				usageId: "0x3A..0x45",
				keywords: "f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 fn function"
			},
			{
				name: "Extended Function Keys (F13-F24)",
				syntax: "\\f13 .. \\f24",
				desc: "Extended function keys F13 through F24 (Usages 0x68..0x73).",
				snippet: "\\f13",
				fields: [
					{ name: "fkey", type: "select", options: ["f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20", "f21", "f22", "f23", "f24"], default: "f13" }
				],
				template: "\\${fkey}",
				usageId: "0x68..0x73",
				keywords: "f13 f14 f15 f16 f17 f18 f19 f20 f21 f22 f23 f24 extended"
			},
			{
				name: "Caps Lock",
				syntax: "\\caps",
				desc: "Caps Lock toggle key.",
				snippet: "\\caps",
				aliases: ["capslock"],
				usageId: "0x39",
				keywords: "caps capslock uppercase toggle lock"
			},
			{
				name: "Num Lock",
				syntax: "\\numlock",
				desc: "Num Lock toggle key.",
				snippet: "\\numlock",
				usageId: "0x53",
				keywords: "numlock keypad number lock"
			},
			{
				name: "Scroll Lock",
				syntax: "\\scrlk",
				desc: "Scroll Lock toggle key.",
				snippet: "\\scrlk",
				aliases: ["scrolllock"],
				usageId: "0x47",
				keywords: "scrlk scrolllock lock key"
			},
			{
				name: "Print Screen / Snapshot",
				syntax: "\\prtscr",
				desc: "Print Screen snapshot key.",
				snippet: "\\prtscr",
				aliases: ["printscreen", "snapshot"],
				usageId: "0x46",
				keywords: "prtscr printscreen screenshot snapshot capture"
			},
			{
				name: "Pause / Break",
				syntax: "\\pause",
				desc: "Pause / Break key.",
				snippet: "\\pause",
				aliases: ["break"],
				usageId: "0x48",
				keywords: "pause break key halt"
			},
			{
				name: "Application / Context Menu",
				syntax: "\\menu",
				desc: "Application menu / Context menu key.",
				snippet: "\\menu",
				aliases: ["app", "contextmenu"],
				usageId: "0x65",
				keywords: "menu app contextmenu rightclick popup"
			},
			{
				name: "Keypad Digits (0-9)",
				syntax: "\\kp_0 .. \\kp_9",
				desc: "Numeric keypad digit keys (Usages 0x59..0x62).",
				snippet: "\\kp_0",
				fields: [
					{ name: "digit", type: "select", options: ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default: "0" }
				],
				template: "\\kp_${digit}",
				usageId: "0x59..0x62",
				keywords: "kp_0 kp_1 kp_2 kp_3 kp_4 kp_5 kp_6 kp_7 kp_8 kp_9 keypad numpad"
			},
			{
				name: "Keypad Operators & Enter",
				syntax: "\\kp_<op>",
				desc: "Numeric keypad operator and action keys (Enter, Plus, Minus, Multiply, Divide, Dot, Equal).",
				snippet: "\\kp_enter",
				fields: [
					{ name: "op", type: "select", options: ["kp_enter", "kp_plus", "kp_minus", "kp_multiply", "kp_star", "kp_divide", "kp_slash", "kp_dot", "kp_period", "kp_equal"], default: "kp_enter" }
				],
				template: "\\${op}",
				usageId: "0x54..0x58, 0x63, 0x67",
				keywords: "kp_enter kp_plus kp_minus kp_multiply kp_star kp_divide kp_slash kp_dot kp_period kp_equal keypad numpad"
			}
		]
	},
	{
		name: "Keyboard: Modifiers, Combos & Holds",
		badge: "Combos",
		desc: "Modifier keys, modifier chaining (+), and physical key hold / release",
		items: [
			{
				name: "Control Modifier",
				syntax: "\\ctrl, \\rctrl",
				desc: "Control modifier key (Left=0xE0, Right=0xE4).",
				snippet: "\\ctrl",
				aliases: ["control", "lctrl", "lcontrol", "rcontrol"],
				usageId: "0xE0, 0xE4",
				keywords: "ctrl control lctrl rctrl modifier"
			},
			{
				name: "Shift Modifier",
				syntax: "\\shift, \\rshift",
				desc: "Shift modifier key (Left=0xE1, Right=0xE5).",
				snippet: "\\shift",
				aliases: ["lshift"],
				usageId: "0xE1, 0xE5",
				keywords: "shift lshift rshift modifier"
			},
			{
				name: "Alt / Option Modifier",
				syntax: "\\alt, \\ralt",
				desc: "Alt / Option modifier key (Left=0xE2, Right=0xE6).",
				snippet: "\\alt",
				aliases: ["opt", "option", "lalt", "ropt", "altgr", "lopt", "loption", "roption"],
				usageId: "0xE2, 0xE6",
				keywords: "alt opt option lalt ralt ropt altgr lopt loption roption modifier"
			},
			{
				name: "GUI / Windows / Command",
				syntax: "\\gui, \\rgui",
				desc: "GUI / Win / Cmd modifier key (Left=0xE3, Right=0xE7).",
				snippet: "\\gui",
				aliases: ["win", "cmd", "command", "lgui", "lwin", "rwin", "lcmd", "rcmd", "lcommand", "rcommand"],
				usageId: "0xE3, 0xE7",
				keywords: "gui win cmd command lgui rgui lwin rwin lcmd rcmd lcommand rcommand"
			},
			{
				name: "Chained Key Combinations",
				syntax: "\\<mod>+<key>",
				desc: "Chains modifiers and keys with + into a simultaneous chord (up to MAX_COMBO_KEYS=8 tokens). Emits KEY_DOWN sequence, click_hold_ms delay, and reverse KEY_UP.",
				snippet: "\\ctrl+c",
				params: [
					{ name: "combo", type: "KeyName (+ KeyName)*", range: "1..8 tokens", desc: "Plus-chained key combination (e.g. \\ctrl+c, \\ctrl+alt+del)" }
				],
				fields: [
					{ name: "mod", type: "select", options: ["ctrl", "alt", "gui", "cmd", "shift", "ctrl+shift", "gui+shift", "alt+shift"], default: "ctrl" },
					{ name: "key", type: "text", default: "c", placeholder: "key" }
				],
				template: "\\${mod}+${key}",
				keywords: "combo shortcut chord chain copy paste"
			},
			{
				name: "Press & Hold Key (\\kdown)",
				syntax: "\\kdown{<combo>}",
				desc: "Physically asserts key or combination down without releasing. Keys remain held until \\kup or execution abort. Empty \\kdown{} emits no actions.",
				snippet: "\\kdown{ctrl}",
				params: [
					{ name: "combo", type: "KeyName | Combo", desc: "Key identifier or + chained keys to press and hold" }
				],
				fields: [
					{ name: "combo", type: "text", default: "ctrl", placeholder: "key / combo" }
				],
				template: "\\kdown{${combo}}",
				keywords: "kdown key hold press down modifier assertion"
			},
			{
				name: "Release Held Key (\\kup)",
				syntax: "\\kup{[<combo>]}",
				desc: "Releases specific held keys; if empty \\kup{}, releases ALL keyboard keys immediately. In hold/release payloads, '0' refers to main-row digit 0 (0x27), not release-all.",
				snippet: "\\kup{ctrl}",
				params: [
					{ name: "combo", type: "KeyName | Combo", desc: "Specific keys to release; leave empty {} for emergency all-key release", optional: true }
				],
				fields: [
					{ name: "combo", type: "text", default: "ctrl", placeholder: "empty for all" }
				],
				template: "\\kup{${combo}}",
				keywords: "kup key release up modifier safety reset"
			}
		]
	},
	{
		name: "Consumer & Multimedia Controls",
		badge: "Media",
		desc: "USB HID Usage Page 0x0C Consumer Control media, volume and power keys",
		items: [
			{
				name: "Volume Up",
				syntax: "\\volup",
				desc: "Increment audio volume.",
				snippet: "\\volup",
				aliases: ["media_volup", "volume_up"],
				usageId: "0x00E9",
				keywords: "volup volume up louder audio sound"
			},
			{
				name: "Volume Down",
				syntax: "\\voldn",
				desc: "Decrement audio volume.",
				snippet: "\\voldn",
				aliases: ["media_voldn", "volume_down"],
				usageId: "0x00EA",
				keywords: "voldn volume down quieter audio sound"
			},
			{
				name: "Mute Audio",
				syntax: "\\mute",
				desc: "Toggle audio mute state.",
				snippet: "\\mute",
				aliases: ["media_mute", "volume_mute"],
				usageId: "0x00E2",
				keywords: "mute audio sound silence toggle quiet"
			},
			{
				name: "Play / Pause",
				syntax: "\\media_playpause",
				desc: "Toggle media playback.",
				snippet: "\\media_playpause",
				aliases: ["playpause", "play_pause"],
				usageId: "0x00CD",
				keywords: "media playpause play pause toggle track music video"
			},
			{
				name: "Play Media",
				syntax: "\\media_play",
				desc: "Start media playback.",
				snippet: "\\media_play",
				aliases: ["play"],
				usageId: "0x00B0",
				keywords: "media play track playback start music"
			},
			{
				name: "Pause Media",
				syntax: "\\media_pause",
				desc: "Pause media playback.",
				snippet: "\\media_pause",
				aliases: ["pause_track"],
				usageId: "0x00B1",
				keywords: "media pause track playback halt music"
			},
			{
				name: "Next Track",
				syntax: "\\media_next",
				desc: "Skip to next media track.",
				snippet: "\\media_next",
				aliases: ["next_track"],
				usageId: "0x00B5",
				keywords: "media next track forward music song"
			},
			{
				name: "Previous Track",
				syntax: "\\media_prev",
				desc: "Skip to previous media track.",
				snippet: "\\media_prev",
				aliases: ["prev_track"],
				usageId: "0x00B6",
				keywords: "media prev track backward previous music song"
			},
			{
				name: "Stop Playback",
				syntax: "\\media_stop",
				desc: "Stop media playback.",
				snippet: "\\media_stop",
				aliases: ["stop_track"],
				usageId: "0x00B7",
				keywords: "media stop track music playback halt"
			},
			{
				name: "Brightness Up",
				syntax: "\\brightup",
				desc: "Increase display brightness.",
				snippet: "\\brightup",
				aliases: ["brightness_up"],
				usageId: "0x006F",
				keywords: "brightup brightness up screen display monitor"
			},
			{
				name: "Brightness Down",
				syntax: "\\brightdn",
				desc: "Decrease display brightness.",
				snippet: "\\brightdn",
				aliases: ["brightness_down"],
				usageId: "0x0070",
				keywords: "brightdn brightness down screen display monitor"
			},
			{
				name: "Host Sleep / Suspend",
				syntax: "\\sys_sleep",
				desc: "Trigger host computer sleep / suspend.",
				snippet: "\\sys_sleep",
				aliases: ["system_sleep", "host_sleep"],
				usageId: "0x0032",
				keywords: "sys_sleep sleep host system suspend standby"
			}
		]
	},
	{
		name: "Mouse Controls",
		badge: "Mouse",
		desc: "Relative pointer movements, button taps with repeat count, and button holds",
		items: [
			{
				name: "Click Mouse Button (\\click)",
				syntax: "\\click{btn[, count]}",
				desc: "Click mouse button count times. Each click holds for click_hold_ms (default 10ms) followed by VM pacing delay.",
				snippet: "\\click{L}",
				params: [
					{ name: "btn", type: "L | R | M | B | F", desc: "Target button: L (Left), R (Right), M (Middle), B (Back/X1), F (Forward/X2)" },
					{ name: "count", type: "u16 integer", range: "1..65535", desc: "Number of consecutive clicks (default: 1; e.g. 2 for double-click)", optional: true }
				],
				fields: [
					{ name: "btn", type: "select", options: ["L", "R", "M", "B", "F"], default: "L" },
					{ name: "count", type: "text", default: "1", placeholder: "count", width: "42px" }
				],
				template: "\\click{${btn}}",
				keywords: "click mouse button l r m b f left right middle double"
			},
			{
				name: "Relative Move (\\move)",
				syntax: "\\move{dx, dy}",
				desc: "Move mouse cursor relative to current position. Driver chunks movements > ±127 into consecutive reports.",
				snippet: "\\move{100, -50}",
				params: [
					{ name: "dx", type: "signed i16", range: "-32768..32767", desc: "Horizontal delta in pixels (+right, -left)" },
					{ name: "dy", type: "signed i16", range: "-32768..32767", desc: "Vertical delta in pixels (+down, -up)" }
				],
				fields: [
					{ name: "dx", type: "text", default: "100", placeholder: "dx", width: "54px" },
					{ name: "dy", type: "text", default: "-50", placeholder: "dy", width: "54px" }
				],
				template: "\\move{${dx}, ${dy}}",
				keywords: "move mouse cursor relative dx dy pointer displacement"
			},
			{
				name: "Mouse Button Down (\\mdown)",
				syntax: "\\mdown{btn}",
				desc: "Press and hold mouse button down without releasing. Safe invariant automatically releases on macro exit/abort.",
				snippet: "\\mdown{L}",
				params: [
					{ name: "btn", type: "L | R | M | B | F", desc: "Target mouse button to assert and hold" }
				],
				fields: [
					{ name: "btn", type: "select", options: ["L", "R", "M", "B", "F"], default: "L" }
				],
				template: "\\mdown{${btn}}",
				keywords: "mdown mouse down hold drag press"
			},
			{
				name: "Mouse Button Up (\\mup)",
				syntax: "\\mup{btn}",
				desc: "Release held mouse button.",
				snippet: "\\mup{L}",
				params: [
					{ name: "btn", type: "L | R | M | B | F", desc: "Target mouse button to release" }
				],
				fields: [
					{ name: "btn", type: "select", options: ["L", "R", "M", "B", "F"], default: "L" }
				],
				template: "\\mup{${btn}}",
				keywords: "mup mouse up release drop"
			}
		]
	},
	{
		name: "Timing & VM Pacing",
		badge: "Timing",
		desc: "Execution delays and task-global inter-action pacing intervals",
		items: [
			{
				name: "Delay Execution (\\delay)",
				syntax: "\\delay{duration}",
				desc: "Explicit pause. Supports dimensioned literals (ms, s) or variable ref ($t). Evaluates active guards every 20ms slice.",
				snippet: "\\delay{100ms}",
				params: [
					{ name: "duration", type: "Duration | Ref", range: "0..65535 ms", desc: "Sleep duration; folded at compile time or clamped at runtime" }
				],
				fields: [
					{ name: "duration", type: "text", default: "100ms", placeholder: "e.g. 100ms / 1.5s" }
				],
				template: "\\delay{${duration}}",
				keywords: "delay sleep pause wait milliseconds ms seconds duration"
			},
			{
				name: "Set VM Pacing (\\pace)",
				syntax: "\\pace{interval}",
				desc: "Sets task-global VM pacing interval automatically inserted after every subsequent atomic keystroke, click and action.",
				snippet: "\\pace{20ms}",
				params: [
					{ name: "interval", type: "Duration | Ref", range: "0..65535 ms", desc: "Inter-action throttle interval; persists across nested scopes" }
				],
				fields: [
					{ name: "interval", type: "text", default: "20ms", placeholder: "e.g. 20ms / 50ms" }
				],
				template: "\\pace{${interval}}",
				keywords: "pace speed throttle rate interval delay auto timing"
			}
		]
	},
	{
		name: "Variables, Arithmetic & Registers",
		badge: "State",
		desc: "General i32 registers x0..x31, arithmetic operations, and read-only system registers",
		items: [
			{
				name: "Declare & Assign (\\set)",
				syntax: "\\set{var}{val}",
				desc: "Declare and assign scalar value or Handle to variable. User variables map 1-to-1 to 32 general registers (0x00..0x1F).",
				snippet: "\\set{x}{100}",
				params: [
					{ name: "var", type: "Identifier", range: "x0..x31 (max 32)", desc: "Target variable name; cannot be ret, err, timeout, or slot" },
					{ name: "val", type: "Value | Handle | Ref", range: "signed i32", desc: "Immediate integer, hex 0x, folded duration, variable ref, or Handle" }
				],
				fields: [
					{ name: "var", type: "text", default: "x", placeholder: "var", width: "50px" },
					{ name: "val", type: "text", default: "100", placeholder: "value", width: "70px" }
				],
				template: "\\set{${var}}{${val}}",
				keywords: "set var variable assign store register i32 integer state"
			},
			{
				name: "In-Place Addition (\\add)",
				syntax: "\\add{var}{val}",
				desc: "Mutate variable in-place by adding val with signed two's-complement 32-bit modular wraparound. Handle in var or val sets $err = ERR_HANDLE_MISUSE (8).",
				snippet: "\\add{x}{1}",
				params: [
					{ name: "var", type: "Identifier", desc: "Previously declared user variable name" },
					{ name: "val", type: "Value | Ref", range: "signed i32", desc: "Scalar value or register to add" }
				],
				fields: [
					{ name: "var", type: "text", default: "x", placeholder: "var", width: "50px" },
					{ name: "val", type: "text", default: "1", placeholder: "value", width: "70px" }
				],
				template: "\\add{${var}}{${val}}",
				returns: "Mutates var in-place (i32 wraparound); Handle sets $err = 8",
				keywords: "add increment plus sum variable math arithmetic"
			},
			{
				name: "In-Place Subtraction (\\sub)",
				syntax: "\\sub{var}{val}",
				desc: "Mutate variable in-place by subtracting val with signed two's-complement 32-bit modular wraparound. Handle in var or val sets $err = ERR_HANDLE_MISUSE (8).",
				snippet: "\\sub{x}{1}",
				params: [
					{ name: "var", type: "Identifier", desc: "Previously declared user variable name" },
					{ name: "val", type: "Value | Ref", range: "signed i32", desc: "Scalar value or register to subtract" }
				],
				fields: [
					{ name: "var", type: "text", default: "x", placeholder: "var", width: "50px" },
					{ name: "val", type: "text", default: "1", placeholder: "value", width: "70px" }
				],
				template: "\\sub{${var}}{${val}}",
				returns: "Mutates var in-place (i32 wraparound); Handle sets $err = 8",
				keywords: "sub decrement minus difference variable math arithmetic"
			},
			{
				name: "Type Decimal Keystrokes (\\typenum)",
				syntax: "\\typenum{ref}",
				desc: "Renders signed 32-bit integer in decimal ASCII (with leading - if negative) and types each character via keyboard HID. Literal produces compile error; Handle sets $err = ERR_HANDLE_MISUSE (8).",
				snippet: "\\typenum{$x}",
				params: [
					{ name: "ref", type: "VarRef | SysVarRef | SlotRef", desc: "Reference to variable ($var), system register ($ret/$err), or slot receptor" }
				],
				fields: [
					{ name: "ref", type: "text", default: "$x", placeholder: "$x / $ret" }
				],
				template: "\\typenum{${ref}}",
				keywords: "typenum type print digits number format decimal output"
			},
			{
				name: "System Registers ($ret / $err / $timeout)",
				syntax: "$ret / $err / $timeout",
				desc: "Read-only system registers: $ret (latest call result / handle ID), $err (status code: 0=ERR_OK, 1=GENERIC, 2=INVALID_ARG, 3=NO_PROVIDER, 4=OUT_OF_BOUNDS, 5=HANDLE_INVALID, 6=RESOURCE_EXHAUSTED, 7=TIMEOUT, 8=HANDLE_MISUSE), $timeout (wait status: 0=arrived/consumed, 1=timeout).",
				snippet: "$ret",
				fields: [
					{ name: "reg", type: "select", options: ["$ret", "$err", "$timeout"], default: "$ret" }
				],
				template: "${reg}",
				returns: "$ret: value/handle; $err: status code (0=OK); $timeout: wait result (0=arrived, 1=timeout)",
				keywords: "ret err timeout return error status register system flag"
			}
		]
	},
	{
		name: "Predicates & Conditionals (\\if)",
		badge: "Logic",
		desc: "Relational condition triplets, short-circuit logic blocks, and forward branching",
		items: [
			{
				name: "Conditional Branch (\\if)",
				syntax: "\\if{ {lhs, op, rhs} }{ then_block } [ { else_block } ]",
				desc: "Forward-only conditional branch. Evaluates predicate (atomic condition requires double braces {{lhs, op, rhs}}; or \\all/\\any logic block); executes then_block if true, optional else_block if false.",
				snippet: "\\if{ {$x, >=, 10} }{\n  \\click{L}\n}{\n  \\delay{50ms}\n}\n",
				multiline: true,
				params: [
					{ name: "predicate", type: "Triplet | LogicBlock", desc: "Condition: atomic triplet {{lhs, op, rhs}} or logic block \\all{...}/\\any{...}" },
					{ name: "then_block", type: "CodeBlock", desc: "Code executed when predicate evaluates to true" },
					{ name: "else_block", type: "CodeBlock", desc: "Optional code executed when predicate evaluates to false", optional: true }
				],
				fields: [
					{ name: "lhs", type: "text", default: "$x", width: "46px" },
					{ name: "op", type: "select", options: [">=", "=", "!=", "<", "<=", ">"], default: ">=" },
					{ name: "rhs", type: "text", default: "10", width: "46px" }
				],
				template: "\\if{ {${lhs}, ${op}, ${rhs}} }{\n  \\click{L}\n}{\n  \\delay{50ms}\n}\n",
				keywords: "if branch condition compare equal relational greater less else"
			},
			{
				name: "Conjunction Logic Block (\\all)",
				syntax: "\\all{ cond1, cond2, ... }",
				desc: "Short-circuit boolean AND across 1 to 8 predicates (triplets or nested \\all/\\any up to max nesting depth 4). Fails fast on first false condition.",
				snippet: "\\if{ \\all{ {$x, >=, 0}, {$x, <=, 100} } }{\n  \\enter\n}\n",
				multiline: true,
				params: [
					{ name: "predicates", type: "Comma-separated predicates", desc: "List of triplets {{lhs, op, rhs}} or logic blocks evaluated conjunctively" }
				],
				fields: [
					{ name: "cond1", type: "text", default: "{$x, >=, 0}", placeholder: "cond1" },
					{ name: "cond2", type: "text", default: "{$x, <=, 100}", placeholder: "cond2" }
				],
				template: "\\if{ \\all{ ${cond1}, ${cond2} } }{\n  \\enter\n}\n",
				keywords: "if all and conjunction logic short-circuit multiple"
			},
			{
				name: "Disjunction Logic Block (\\any)",
				syntax: "\\any{ cond1, cond2, ... }",
				desc: "Short-circuit boolean OR across 1 to 8 predicates (triplets or nested \\all/\\any up to max nesting depth 4). Succeeds immediately on first true condition.",
				snippet: "\\if{ \\any{ {$err, !=, 0}, {$timeout, =, 1} } }{\n  \\esc\n}\n",
				multiline: true,
				params: [
					{ name: "predicates", type: "Comma-separated predicates", desc: "List of triplets {{lhs, op, rhs}} or logic blocks evaluated disjunctively" }
				],
				fields: [
					{ name: "cond1", type: "text", default: "{$err, !=, 0}", placeholder: "cond1" },
					{ name: "cond2", type: "text", default: "{$timeout, =, 1}", placeholder: "cond2" }
				],
				template: "\\if{ \\any{ ${cond1}, ${cond2} } }{\n  \\esc\n}\n",
				keywords: "if any or disjunction logic short-circuit error timeout"
			}
		]
	},
	{
		name: "Guarded Execution (\\guarded)",
		badge: "Safety",
		desc: "Continuous safety invariants, throttling intervals, and automatic peripheral release on violation",
		items: [
			{
				name: "Guarded Block (\\guarded)",
				syntax: "\\guarded[throttle]{ {invariant} }{ body } [ { abort_block } ]",
				desc: "Protected execution block. Evaluates invariant before every action and delay slice. Optional [throttle] specifies minimum ms between evaluations. Violation releases all keys/buttons/consumer usage, rolls back loop frames, and executes optional abort_block.",
				snippet: "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n}{\n  \\esc\n}\n",
				multiline: true,
				params: [
					{ name: "throttle", type: "Duration", range: "0..65535 ms", desc: "Optional minimum interval between re-evaluations (default: 0ms); dynamic refs not allowed", optional: true },
					{ name: "invariant", type: "GuardTriplet | Flat \\all/\\any", desc: "Safety condition checked continuously: {{lhs, op, rhs}} or flat \\all/\\any (1-8 triplets, no nesting)" },
					{ name: "body", type: "CodeBlock", desc: "Protected code stream" },
					{ name: "abort_block", type: "CodeBlock", desc: "Optional recovery code executed if invariant fails", optional: true }
				],
				fields: [
					{ name: "cond", type: "select", options: ["{$err, =, 0}", "{$timeout, =, 0}"], default: "{$err, =, 0}" }
				],
				template: "\\guarded{ ${cond} }{\n  \\ctrl+c\n}{\n  \\esc\n}\n",
				keywords: "guarded invariant safe safety abort protect release exception rollback"
			},
			{
				name: "Throttled Guard (\\guarded[throttle])",
				syntax: "\\guarded[throttle]{ {invariant} }{ body } [ { abort_block } ]",
				desc: "Guard with explicit throttle interval to prevent high CPU or network inspection overhead.",
				snippet: "\\guarded[50ms]{ {$err, =, 0} }{\n  \\click{L}\n}\n",
				multiline: true,
				params: [
					{ name: "throttle", type: "Duration", range: "0..65535 ms", desc: "Static minimum interval between re-evaluations in ms" },
					{ name: "invariant", type: "GuardTriplet | Flat \\all/\\any", desc: "Safety condition checked continuously: {{lhs, op, rhs}} or flat \\all/\\any (1-8 triplets, no nesting)" },
					{ name: "body", type: "CodeBlock", desc: "Protected code stream" },
					{ name: "abort_block", type: "CodeBlock", desc: "Optional recovery code executed if invariant fails", optional: true }
				],
				fields: [
					{ name: "throttle", type: "text", default: "50ms", placeholder: "throttle", width: "50px" },
					{ name: "cond", type: "select", options: ["{$err, =, 0}", "{$timeout, =, 0}"], default: "{$err, =, 0}" }
				],
				template: "\\guarded[${throttle}]{ ${cond} }{\n  \\click{L}\n}\n",
				keywords: "guarded throttle interval period guard rate rate-limit"
			}
		]
	},
	{
		name: "Bounded Counting Loops (\\rep)",
		badge: "Loops",
		desc: "Bounded counting iterations with strict frame stack caps and backward jump edges",
		items: [
			{
				name: "Counted Loop (\\rep)",
				syntax: "\\rep{count}{ body }",
				desc: "Executes body count times. Count of 0 skips body. Max loop nesting depth: 8. Guarantees halting decidability.",
				snippet: "\\rep{10}{\n  \\click{L}\n  \\delay{50ms}\n}\n",
				multiline: true,
				params: [
					{ name: "count", type: "u8 literal | $var", range: "0..255", desc: "Number of iterations; out-of-range static count is compile error" },
					{ name: "body", type: "CodeBlock", desc: "Code stream executed on each iteration" }
				],
				fields: [
					{ name: "count", type: "text", default: "10", placeholder: "count", width: "50px" }
				],
				template: "\\rep{${count}}{\n  \\click{L}\n  \\delay{50ms}\n}\n",
				keywords: "rep repeat loop count times iteration bounded cycle"
			}
		]
	},
	{
		name: "Compile-Time Text Macros (\\def)",
		badge: "Macros",
		desc: "Compile-time textual macros with parameter substitution (#1..#9)",
		items: [
			{
				name: "Define Macro (\\def)",
				syntax: "\\def{name}[argc]{ body }",
				desc: "Defines inline compile-time text macro. Must be defined at document root. Expanded inline up to max depth 16.",
				snippet: "\\def{paste_enter}[0]{\\ctrl+v\\enter}\n",
				params: [
					{ name: "name", type: "Identifier", desc: "Macro name (>= 2 characters; cannot conflict with built-in commands or keys)" },
					{ name: "argc", type: "0..9", desc: "Number of positional arguments expected (default: 0)", optional: true },
					{ name: "body", type: "CodeBlock", desc: "Expansion body; parameter placeholders #1..#9, ## for literal #" }
				],
				fields: [
					{ name: "name", type: "text", default: "paste_enter", width: "85px" },
					{ name: "argc", type: "select", options: ["0", "1", "2"], default: "0" },
					{ name: "body", type: "text", default: "\\ctrl+v\\enter", placeholder: "body" }
				],
				template: "\\def{${name}}[${argc}]{${body}}\n",
				keywords: "def macro function inline define expand template zero"
			},
			{
				name: "Parameterized Macro Example",
				syntax: "\\def{name}[argc]{ ... #1 ... }",
				desc: "User macro accepting arguments, substituted transparently into commands, values, or text blocks.",
				snippet: "\\def{select_wrap}[2]{\\kdown{shift}#1\\kup{shift}#2}\n",
				multiline: true,
				params: [
					{ name: "name", type: "Identifier", desc: "Macro name (>= 2 characters)" },
					{ name: "argc", type: "1..9", desc: "Number of positional arguments expected" },
					{ name: "body", type: "CodeBlock", desc: "Expansion template with #1..#9 parameter replacement" }
				],
				fields: [
					{ name: "name", type: "text", default: "select_wrap", width: "85px" },
					{ name: "argc", type: "select", options: ["1", "2"], default: "2" },
					{ name: "body", type: "text", default: "\\kdown{shift}#1\\kup{shift}#2", placeholder: "body" }
				],
				template: "\\def{${name}}[${argc}]{${body}}\n",
				keywords: "def macro parameter args substitution expand user"
			}
		]
	},
	{
		name: "Signals, Receptor Slots & Waiting",
		badge: "Events",
		desc: "External event projection, value receptors, and asynchronous event suspension",
		items: [
			{
				name: "Slot Receptor ($slot)",
				syntax: "$slot.<sig>.<field>",
				desc: "Read external signal slot receptor. Split at last dot: left side is signal channel, last segment is field name. Up to 256 distinct receptors.",
				snippet: "$slot.net.msg.val",
				params: [
					{ name: "sig", type: "SignalName", desc: "External signal channel name (e.g. net.msg, img.ready)" },
					{ name: "field", type: "FieldName", desc: "Receptor field name after last dot (e.g. val)" }
				],
				fields: [
					{ name: "slot", type: "select", options: ["$slot.net.msg.val", "$slot.img.ready.val"], default: "$slot.net.msg.val" }
				],
				template: "${slot}",
				returns: "Latest payload scalar (i32)",
				keywords: "slot signal field receptor state event projection async"
			},
			{
				name: "Wait for Signal (\\wait)",
				syntax: "\\wait[timeout]{ $slot }",
				desc: "Suspends execution until external signal projection arrives or timeout expires. Positive timeout in ms; nonpositive (<= 0) performs immediate non-blocking poll. Sets $timeout=0 on arrival, 1 on timeout. Requires preceding \\use{ns}.",
				snippet: "\\use{net}\n\\wait[5s]{ $slot.net.msg.val }",
				params: [
					{ name: "timeout", type: "Duration | signed i32", range: "-2147483648..2147483647 ms", desc: "Wait timeout in ms (default: ~24.8 days / 2147483647ms). Values <= 0 perform immediate non-blocking poll.", optional: true },
					{ name: "slot", type: "SlotRef", desc: "Target receptor ($slot.<sig>.<field> or ${slot.<sig>.<field>})" }
				],
				fields: [
					{ name: "timeout", type: "text", default: "5s", placeholder: "e.g. 5s / 0 for poll", width: "55px" },
					{ name: "slot", type: "select", options: ["$slot.net.msg.val", "$slot.img.ready.val"], default: "$slot.net.msg.val" }
				],
				template: "\\use{net}\n\\wait[${timeout}]{ ${slot} }",
				returns: "$timeout: 0 = arrived/consumed, 1 = timeout",
				keywords: "wait timeout suspend event signal arrival latch async poll nonblocking"
			}
		]
	},
	{
		name: "Runtime Extensions: sys & net",
		badge: "Extensions",
		desc: "System diagnostics and network protocol v1 messaging namespaces",
		items: [
			{
				name: "Activate Namespace (\\use)",
				syntax: "\\use{ns}",
				desc: "Idempotent compile-time directive activating standard or custom runtime namespace. Required before \\call, signals, or slots.",
				snippet: "\\use{sys}\n",
				params: [
					{ name: "ns", type: "Identifier", desc: "Namespace to activate: sys, net, img, etc." }
				],
				fields: [
					{ name: "ns", type: "select", options: ["sys", "net", "img"], default: "sys" }
				],
				template: "\\use{${ns}}\n",
				keywords: "use namespace activate import extension module"
			},
			{
				name: "Query Free Heap (sys.free)",
				syntax: "\\call{sys.free}",
				desc: "Queries device available free heap RAM in bytes.",
				snippet: "\\use{sys}\n\\call{sys.free}",
				returns: "$ret = free heap bytes (i32), $err = 0 (ERR_OK)",
				keywords: "use sys call free heap memory ram system diagnostic"
			},
			{
				name: "Broadcast Message (net.send)",
				syntax: "\\call{net.send}{tag}",
				desc: "Broadcasts a net.msg frame labeled tag to all authenticated Protocol v1 companion host clients over TCP :7373.",
				snippet: "\\use{net}\n\\call{net.send}{sync}",
				params: [
					{ name: "tag", type: "Name identifier", desc: "Message label string emitted to connected clients" }
				],
				fields: [
					{ name: "tag", type: "text", default: "sync", placeholder: "tag", width: "70px" }
				],
				template: "\\use{net}\n\\call{net.send}{${tag}}",
				returns: "$ret = client count reached (i32), $err = 0 (ERR_OK)",
				keywords: "use net call send broadcast network message host sync"
			}
		]
	},
	{
		name: "Vision & Pixel Processing: img",
		badge: "Vision",
		desc: "Exact pixel matching family, push-model screen watchers, and pixel matrix literals",
		items: [
			{
				name: "Capture Screen Rect (img.snap)",
				syntax: "\\call{img.snap}{x, y, w, h}",
				desc: "Captures screen rectangle (native resolution). Allocates and materializes a snapshot Handle in $ret. Dimensions up to 128x128.",
				snippet: "\\use{img}\n\\call{img.snap}{0, 0, 100, 100}",
				params: [
					{ name: "rect", type: "x, y, w, h", range: "x,y >= 0; 1 <= w,h <= 128", desc: "Compound group: top-left coordinates and dimensions in pixels" }
				],
				fields: [
					{ name: "x", type: "text", default: "0", width: "36px" },
					{ name: "y", type: "text", default: "0", width: "36px" },
					{ name: "w", type: "text", default: "100", width: "42px" },
					{ name: "h", type: "text", default: "100", width: "42px" }
				],
				template: "\\use{img}\n\\call{img.snap}{${x}, ${y}, ${w}, ${h}}",
				returns: "$ret = snap Handle ID (> 0), $err = 0 (ERR_OK, or ERR_NO_PROVIDER=3 / ERR_OUT_OF_BOUNDS=4 / ERR_INVALID_ARG=2 / ERR_RESOURCE_EXHAUSTED=6 / ERR_TIMEOUT=7)",
				keywords: "use img call snap snapshot capture screen framebuffer handle vision"
			},
			{
				name: "Sample Pixel Color (img.at)",
				syntax: "\\call{img.at}{$snap}{x, y}",
				desc: "Samples 24-bit RGB pixel color from snapshot Handle at local (x, y) coordinates.",
				snippet: "\\use{img}\n\\call{img.snap}{0, 0, 1, 1}\n\\set{s}{$ret}\n\\call{img.at}{$s}{0, 0}",
				params: [
					{ name: "snap", type: "Handle", desc: "Snapshot Handle reference ($ret or variable)" },
					{ name: "point", type: "x, y", range: "0 <= x < w, 0 <= y < h", desc: "Snapshot-local pixel coordinates" }
				],
				fields: [
					{ name: "snap", type: "text", default: "$s", width: "42px" },
					{ name: "x", type: "text", default: "0", width: "36px" },
					{ name: "y", type: "text", default: "0", width: "36px" }
				],
				template: "\\call{img.at}{${snap}}{${x}, ${y}}",
				returns: "$ret = packed 0xRRGGBB color scalar (i32), $err = 0 (ERR_OK, or ERR_HANDLE_INVALID=5 / ERR_OUT_OF_BOUNDS=4)",
				keywords: "call img at pixel color rgb sample point coord inspection"
			},
			{
				name: "Compare Snapshots (img.diff)",
				syntax: "\\call{img.diff}{$snapA}{$snapB}{tol}",
				desc: "Compares two snapshots pixel-wise. Counts pixels differing in any channel by more than tol. tol=0 requires exact match.",
				snippet: "\\use{img}\n\\call{img.snap}{0, 0, 1, 1}\n\\set{a}{$ret}\n\\call{img.snap}{0, 0, 1, 1}\n\\set{b}{$ret}\n\\call{img.diff}{$a}{$b}{16}",
				params: [
					{ name: "snapA", type: "Handle", desc: "First snapshot Handle reference" },
					{ name: "snapB", type: "Handle", desc: "Second snapshot Handle reference" },
					{ name: "tol", type: "u8 integer", range: "0..255", desc: "Per-channel tolerance threshold" }
				],
				fields: [
					{ name: "snapA", type: "text", default: "$a", width: "42px" },
					{ name: "snapB", type: "text", default: "$b", width: "42px" },
					{ name: "tol", type: "text", default: "16", width: "42px" }
				],
				template: "\\call{img.diff}{${snapA}}{${snapB}}{${tol}}",
				returns: "$ret = count of differing pixels (i32), $err = 0 (ERR_OK, or ERR_HANDLE_INVALID=5 / ERR_INVALID_ARG=2)",
				keywords: "call img diff compare difference tolerance match count delta"
			},
			{
				name: "Telemetry Point Watcher (img.watch)",
				syntax: "\\call{img.watch}{name}{x, y}{interval_ms, duration_ms}",
				desc: "Initiates continuous background screen point telemetry pushed from host provider to $slot.img.<name>.val.",
				snippet: "\\use{img}\n\\call{img.watch}{ready}{400, 200}{50, 10000}",
				params: [
					{ name: "name", type: "Identifier", desc: "Subscription stream name; projects to $slot.img.<name>.val" },
					{ name: "point", type: "x, y", range: "x,y >= 0", desc: "Screen coordinate to sample" },
					{ name: "span", type: "interval, duration", range: "> 0 ms", desc: "Host sampling interval and mandatory stream lease duration in ms" }
				],
				fields: [
					{ name: "name", type: "text", default: "ready", width: "55px" },
					{ name: "x", type: "text", default: "400", width: "38px" },
					{ name: "y", type: "text", default: "200", width: "38px" },
					{ name: "interval", type: "text", default: "50", width: "38px" },
					{ name: "duration", type: "text", default: "10000", width: "50px" }
				],
				template: "\\use{img}\n\\call{img.watch}{${name}}{${x}, ${y}}{${interval}, ${duration}}",
				returns: "$ret = 0, $err = 0 (ERR_OK, or ERR_NO_PROVIDER=3 / ERR_TIMEOUT=7 / ERR_OUT_OF_BOUNDS=4 / ERR_INVALID_ARG=2)",
				keywords: "call img watch stream monitor observer background predicate telemetry"
			},
			{
				name: "Cancel Watcher (img.unwatch)",
				syntax: "\\call{img.unwatch}{name}",
				desc: "Terminates active telemetry stream and unregisters its projection slot.",
				snippet: "\\call{img.unwatch}{ready}",
				params: [
					{ name: "name", type: "Identifier", desc: "Active subscription stream identifier to terminate" }
				],
				fields: [
					{ name: "name", type: "text", default: "ready", width: "60px" }
				],
				template: "\\call{img.unwatch}{${name}}",
				returns: "$ret = 0, $err = 0 (ERR_OK)",
				keywords: "call img unwatch stop cancel unsubscribe stream"
			},
			{
				name: "RGB Color Immediate (\\rgb)",
				syntax: "\\rgb{r, g, b}",
				desc: "Packs three 8-bit color channels into a 24-bit (r << 16) | (g << 8) | b integer scalar immediate (0xRRGGBB) at compile time.",
				snippet: "\\rgb{255, 0, 0}",
				params: [
					{ name: "r, g, b", type: "u8, u8, u8", range: "0..255", desc: "Red, green, and blue color channels" }
				],
				fields: [
					{ name: "r", type: "text", default: "255", width: "38px" },
					{ name: "g", type: "text", default: "0", width: "38px" },
					{ name: "b", type: "text", default: "0", width: "38px" }
				],
				template: "\\rgb{${r}, ${g}, ${b}}",
				keywords: "rgb color literal red green blue packed 0x immediate hex"
			},
			{
				name: "Pixel Matrix Literal (\\pixelrect)",
				syntax: "\\pixelrect{var}{\\row{c1, c2, ...}, ...}",
				desc: "Compiles reference pixel pattern into constant arena; materializes a read-only snapshot Handle into var on execution via compiler intrinsic sys.const.",
				snippet: "\\pixelrect{target}{\\row{0xFF0000, 0x00FF00}, \\row{0x0000FF, 0xFFFFFF}}",
				params: [
					{ name: "var", type: "Identifier", desc: "Destination variable receiving the snapshot Handle" },
					{ name: "rows", type: "\\row{...} list", range: "edge <= 128", desc: "Equal-length matrix rows of 24-bit colors (hex, dec, or \\rgb)" }
				],
				fields: [
					{ name: "var", type: "text", default: "target", width: "60px" }
				],
				template: "\\pixelrect{${var}}{\\row{0xFF0000, 0x00FF00}, \\row{0x0000FF, 0xFFFFFF}}",
				returns: "Materializes Handle in var; sets $err = 0 on success",
				keywords: "pixelrect row rect literal matrix template const handle snapshot"
			}
		]
	},
	{
		name: "Canonical Workflow Recipes",
		badge: "Recipes",
		desc: "End-to-end production scripts showcasing best practices, safety guards, and timing",
		items: [
			{
				name: "Safe Rapid Clicker",
				desc: "Rapid clicking loop using free layout and explicit 20ms VM action pacing to prevent host driver buffer drops.",
				snippet: "\\#!syntax: free\n\\pace{20ms}\n\\rep{50}{\n  \\click{L}\n  \\delay{100ms}\n}\n",
				multiline: true,
				keywords: "recipe rapid clicker repeat loop pace free syntax"
			},
			{
				name: "Guarded Safe Clipboard",
				desc: "Guarded shortcut execution. If any fault occurs, all keys are safely released before running the abort recovery block.",
				snippet: "\\#!syntax: free\n\\guarded{ {$err, =, 0} }{\n  \\kdown{ctrl}\n  c\n  \\kup{ctrl}\n  \\delay{100ms}\n  \\kdown{ctrl}\n  v\n  \\kup{ctrl}\n}\n",
				multiline: true,
				keywords: "recipe copy paste guarded invariant safe release keys"
			},
			{
				name: "Color Inspection & Branch",
				desc: "Captures 1x1 screen pixel, samples RGB color into $ret, and conditionally clicks only if the pixel matches green (0x00FF00).",
				snippet: "\\#!syntax: free\n\\use{img}\n\\pace{10ms}\n\\call{img.snap}{500, 300, 1, 1}\n\\set{s}{$ret}\n\\call{img.at}{$s}{0, 0}\n\\if{ {$ret, =, 0x00FF00} }{\n  \\click{L}\n}{\n  \\delay{100ms}\n}\n",
				multiline: true,
				keywords: "recipe pixel color inspection sample green click condition img snap"
			},
			{
				name: "Stream Watcher Sync",
				desc: "Subscribes to telemetry stream, suspends via \\wait for slot arrival latch, and triggers action upon arrival.",
				snippet: "\\#!syntax: free\n\\use{img}\n\\call{img.watch}{ready}{400, 200}{50, 10000}\n\\wait[10s]{ $slot.img.ready.val }\n\\if{ {$timeout, =, 0} }{\n  \\click{L}\n}{\n  \\media_stop\n}\n",
				multiline: true,
				keywords: "recipe watch stream observer wait signal slot timeout event"
			},
			{
				name: "Dynamic Counter Loop",
				desc: "Loops 5 times, increments variable x, and types out decimal numbers 1 through 5 using \\typenum.",
				snippet: "\\#!syntax: free\n\\set{x}{0}\n\\rep{5}{\n  \\add{x}{1}\n  \\typenum{$x}\n  \\space\n}\n",
				multiline: true,
				keywords: "recipe counter loop add typenum dynamic numbers"
			}
		]
	}
];
