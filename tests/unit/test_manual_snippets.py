import pytest
from macro import Macro
from mock_hid import MockHID
from runtime import Runtime

@pytest.fixture
def macro():
    rt = Runtime()
    rt.register_ns("sys", {
        "free": ([], "int", lambda: 10000),
    })
    rt.register_signal("net.msg")
    rt.register_ns("net", {
        "send": (["str"], "int", lambda tag: 1),
    })
    rt.register_ns("img", {
        "snap": (["rect"], "snap", lambda x, y, w, h: 1),
        "at": (["snap", "point"], "color", lambda s, x, y: 0),
        "diff": (["snap", "snap", "int"], "int", lambda a, b, tol: 0),
        "watch": (["str", "point", "span"], "int", lambda name, x, y, interval, duration: 1),
        "unwatch": (["str"], "int", lambda name: 1),
    })
    return Macro(MockHID(), rt)

# Complete inventory of snippets presented in the Reference Manual
MANUAL_SNIPPETS = [
    # Preamble & Layout
    "\\#!syntax: free\n",
    "\\#!syntax: typewriter\n",
    "\\#!syntax: newline=ignore, space=emit\n",

    # Escapes, Comments & Verbatim Text
    "\\\\",
    "\\{",
    "\\}",
    "\\$",
    "\\# comment\n",
    "\\\n",
    "\\text{}echo \"Hello World\"\\endtext{}",

    # Keyboard: Editing & Navigation
    "\\enter",
    "\\return",
    "\\esc",
    "\\escape",
    "\\bs",
    "\\backspace",
    "\\tab",
    "\\space",
    "\\del",
    "\\delete",
    "\\ins",
    "\\insert",
    "\\home",
    "\\end",
    "\\pgup",
    "\\pageup",
    "\\pgdn",
    "\\pagedown",
    "\\up",
    "\\down",
    "\\left",
    "\\right",
    "\\minus",
    "\\equal",
    "\\plus",

    # Keyboard: Function, System & Keypad
    "\\f1",
    "\\f5",
    "\\f12",
    "\\f13",
    "\\caps",
    "\\capslock",
    "\\numlock",
    "\\scrlk",
    "\\scrolllock",
    "\\prtscr",
    "\\printscreen",
    "\\snapshot",
    "\\pause",
    "\\break",
    "\\menu",
    "\\app",
    "\\contextmenu",
    "\\kp_0",
    "\\kp_1",
    "\\kp_9",
    "\\kp_enter",
    "\\kp_plus",
    "\\kp_minus",
    "\\kp_multiply",
    "\\kp_star",
    "\\kp_divide",
    "\\kp_slash",
    "\\kp_dot",
    "\\kp_period",
    "\\kp_equal",

    # Keyboard: Modifiers, Combos & Holds
    "\\ctrl",
    "\\control",
    "\\lctrl",
    "\\rctrl",
    "\\shift",
    "\\lshift",
    "\\rshift",
    "\\alt",
    "\\opt",
    "\\option",
    "\\lalt",
    "\\ralt",
    "\\gui",
    "\\win",
    "\\cmd",
    "\\command",
    "\\lgui",
    "\\rgui",
    "\\ctrl+c",
    "\\ctrl+v",
    "\\ctrl+x",
    "\\ctrl+z",
    "\\ctrl+a",
    "\\ctrl+s",
    "\\gui+r",
    "\\cmd+space",
    "\\alt+tab",
    "\\kdown{ctrl}",
    "\\kup{ctrl}",
    "\\kup{}",

    # Consumer & Multimedia Controls
    "\\volup",
    "\\volume_up",
    "\\media_volup",
    "\\voldn",
    "\\volume_down",
    "\\media_voldn",
    "\\mute",
    "\\media_mute",
    "\\volume_mute",
    "\\media_playpause",
    "\\playpause",
    "\\play_pause",
    "\\media_next",
    "\\next_track",
    "\\media_prev",
    "\\prev_track",
    "\\media_stop",
    "\\stop_track",
    "\\media_play",
    "\\play",
    "\\media_pause",
    "\\pause_track",
    "\\brightup",
    "\\brightness_up",
    "\\brightdn",
    "\\brightness_down",
    "\\sys_sleep",
    "\\system_sleep",
    "\\host_sleep",

    # Mouse Commands
    "\\click{L}",
    "\\click{R}",
    "\\click{M}",
    "\\click{B}",
    "\\click{F}",
    "\\click{L, 2}",
    "\\click{L, 3}",
    "\\move{100, -50}",
    "\\mdown{L}",
    "\\mup{L}",

    # Timing & Pacing
    "\\delay{100ms}",
    "\\delay{1.5s}",
    "\\pace{20ms}",

    # Variables, Arithmetic & Registers
    "\\set{x}{100}",
    "\\set{x}{10}\\add{x}{1}",
    "\\set{x}{10}\\sub{x}{1}",
    "\\set{x}{10}\\typenum{$x}",

    # Predicates & Conditionals (\if)
    "\\set{x}{10}\\if{ {$x, >=, 10} }{\\click{L}}{\\delay{50ms}}",
    "\\set{x}{10}\\if{ \\all{ {$x, >=, 0}, {$x, <=, 100} } }{\\enter}",
    "\\if{ \\any{ {$err, !=, 0}, {$timeout, =, 1} } }{\\esc}",

    # Guarded Execution (\guarded)
    "\\guarded{ {$err, =, 0} }{\\ctrl+c}{\\esc}",
    "\\guarded[50ms]{ {$err, =, 0} }{\\click{L}}",

    # Bounded Loops & Flow
    "\\rep{10}{\\click{L}\\delay{50ms}}",

    # Compile-Time User Macros (\def)
    "\\def{paste_enter}[0]{\\ctrl+v\\enter}\\paste_enter{}",
    "\\def{select_wrap}[2]{\\kdown{shift}#1\\kup{shift}#2}\\select_wrap{a}{b}",

    # Signals, Slots & Waiting
    "\\use{net}\\wait[5s]{ $slot.net.msg.val }",
    "\\use{net}\\wait[0]{ $slot.net.msg.val }",

    # Runtime Extensions (sys, net, img)
    "\\use{sys}\n\\call{sys.free}",
    "\\use{net}\n\\call{net.send}{sync}",
    "\\use{img}\n\\call{img.snap}{0, 0, 100, 100}",
    "\\use{img}\n\\call{img.snap}{0, 0, 1, 1}\\set{s}{$ret}\\call{img.at}{$s}{0, 0}",
    "\\use{img}\n\\call{img.snap}{0, 0, 1, 1}\\set{a}{$ret}\\call{img.snap}{0, 0, 1, 1}\\set{b}{$ret}\\call{img.diff}{$a}{$b}{16}",
    "\\use{img}\n\\call{img.watch}{ready}{400, 200}{50, 10000}",
    "\\use{img}\n\\call{img.unwatch}{ready}",
    "\\set{c}{\\rgb{255, 0, 0}}",
    "\\pixelrect{target}{\\row{0xFF0000, 0x00FF00}, \\row{0x0000FF, 0xFFFFFF}}",

    # Canonical Recipes
    "\\#!syntax: free\n\\pace{20ms}\n\\rep{50}{\n  \\click{L}\n  \\delay{100ms}\n}\n",
    "\\#!syntax: free\n\\guarded{ {$err, =, 0} }{\n  \\kdown{ctrl}\n  c\n  \\kup{ctrl}\n  \\delay{100ms}\n  \\kdown{ctrl}\n  v\n  \\kup{ctrl}\n}\n",
    "\\#!syntax: free\n\\use{img}\n\\pace{10ms}\n\\call{img.snap}{500, 300, 1, 1}\n\\set{s}{$ret}\n\\call{img.at}{$s}{0, 0}\n\\if{ {$ret, =, 0x00FF00} }{\n  \\click{L}\n}{\n  \\delay{100ms}\n}\n",
    "\\#!syntax: free\n\\use{img}\n\\call{img.watch}{ready}{400, 200}{50, 10000}\n\\wait[10s]{ $slot.img.ready.val }\n\\if{ {$timeout, =, 0} }{\n  \\click{L}\n}{\n  \\media_stop\n}\n",
    "\\#!syntax: free\n\\set{x}{0}\n\\rep{5}{\n  \\add{x}{1}\n  \\typenum{$x}\n  \\space\n}\n"
]

@pytest.mark.parametrize("snippet", MANUAL_SNIPPETS)
def test_manual_snippet_compiles(macro, snippet):
    # Every snippet presented in the manual must compile cleanly with 0 errors
    bc, err = macro.compile(snippet)
    assert err is None, f"Snippet failed compilation: {snippet!r} -> {err}"
    assert bc is not None and len(bc) > 0
