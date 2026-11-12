#!/usr/bin/env node
import assert from "node:assert/strict";
import {
	detectToken,
	checkBracketBalance,
	detectStructuralScope,
	isInsideVerbatim,
	COMMAND_CANDIDATES,
	SYNTAX_CANDIDATES,
	ESCAPE_CANDIDATES,
	MOUSE_BUTTON_CANDIDATES,
	KEY_NAME_CANDIDATES,
	NAMESPACE_CANDIDATES,
	EXT_FUNCTION_CANDIDATES,
	PREDICATE_OP_CANDIDATES,
	PREDICATE_LOGIC_CANDIDATES
} from "../src/modules/token-detector.ts";

console.log("=========================================");
console.log("Running Token Inspector Comprehensive Suite");
console.log("=========================================\n");

let passed = 0;
let total = 0;

function test(name, fn) {
	total++;
	try {
		fn();
		console.log(`✓ ${name}`);
		passed++;
	} catch (err) {
		console.error(`✗ ${name}`);
		console.error(err);
		process.exit(1);
	}
}

function simulateReplace(text, result, candidate) {
	const start = candidate.tokenStart ?? result.tokenStart;
	const end = candidate.tokenEnd ?? result.tokenEnd;
	return text.slice(0, start) + candidate.insert + text.slice(end);
}

// 1. Test \ alone
test("1. Alone '\\' matches commands, escapes, top syntax presets, and user macros", () => {
	const text = "\\";
	const res = detectToken(text, 1);
	assert.ok(res !== null, "Result should not be null");
	assert.equal(res.context, "Command / Key");
	assert.equal(res.tokenStart, 0);
	assert.equal(res.tokenEnd, 1);
	assert.ok(res.matches.length > 20, "Should have rich matches");
	assert.ok(res.matches.some(m => m.label === "\\set"));
	assert.ok(res.matches.some(m => m.label === "\\click"));
	assert.ok(res.matches.some(m => m.label === "\\enter"));
	assert.ok(res.matches.some(m => m.label === "\\#!syntax: free"));
});

// 2. Test \# and \#! directives and comments (deduplicated)
test("2. Line prefix '\\#' and '\\#!' match comments and syntax directives with clean replacement", () => {
	// 2a. Line start \# matches comment and all syntax directives
	const textHash = "\\#";
	const resHash = detectToken(textHash, 2);
	assert.ok(resHash !== null);
	assert.equal(resHash.context, "Comment / Directive");
	assert.equal(resHash.rawToken, "\\#");
	const labelsHash = resHash.matches.map(m => m.label);
	assert.ok(labelsHash.includes("\\#"));
	assert.ok(labelsHash.includes("\\#!syntax: free"));
	assert.ok(labelsHash.includes("\\#!syntax: typewriter"));

	const candFree = resHash.matches.find(m => m.label === "\\#!syntax: free");
	assert.equal(simulateReplace(textHash, resHash, candFree), "\\#!syntax: free\n");

	// 2b. Line start \#! and \#!syn
	const textExcl = "\\#!syn";
	const resExcl = detectToken(textExcl, 6);
	assert.ok(resExcl !== null);
	assert.equal(resExcl.context, "Syntax Directive");
	assert.equal(resExcl.rawToken, "\\#!syn");
	assert.ok(resExcl.matches.length >= 8);

	// 2c. Fine-grained filter \#!syntax: n
	const textFilter = "\\#!syntax: n";
	const resFilter = detectToken(textFilter, 12);
	assert.ok(resFilter !== null);
	const labelsFilter = resFilter.matches.map(m => m.label);
	assert.ok(labelsFilter.includes("\\#!syntax: newline=ignore, space=emit"));
	assert.ok(!labelsFilter.includes("\\#!syntax: free"));
});

// 3. Test \set
test("3. Command '\\set' matches \\set accurately and replaces without duplicate prefix", () => {
	const text = "\\set";
	const res = detectToken(text, 4);
	assert.ok(res !== null);
	assert.equal(res.context, "Command / Key");
	assert.equal(res.rawToken, "\\set");
	assert.equal(res.tokenStart, 0);
	assert.equal(res.tokenEnd, 4);

	const cand = res.matches.find(m => m.label === "\\set");
	assert.ok(cand !== undefined, "\\set candidate must exist");
	const replaced = simulateReplace(text, res, cand);
	assert.equal(replaced, "\\set{x}{100}", "Replaced text must be \\set{x}{100}");
});

// 4. Test variable reference $ and $ret
test("4. Variable reference '$' and '$ret' with user variable scanning and delimited ${} form", () => {
	// 4a. Lone '$'
	const text1 = "$";
	const res1 = detectToken(text1, 1);
	assert.ok(res1 !== null);
	assert.equal(res1.context, "Variable / Slot");
	assert.equal(res1.tokenStart, 0);
	assert.equal(res1.tokenEnd, 1);
	const labels1 = res1.matches.map(m => m.label);
	assert.ok(labels1.includes("$ret"));
	assert.ok(labels1.includes("$err"));
	assert.ok(labels1.includes("$timeout"));
	assert.ok(labels1.includes("$slot.net.msg.val"));

	// 4b. '$ret'
	const text2 = "$ret";
	const res2 = detectToken(text2, 4);
	assert.ok(res2 !== null);
	const candRet = res2.matches.find(m => m.label === "$ret");
	assert.ok(candRet !== undefined);
	assert.equal(simulateReplace(text2, res2, candRet), "$ret");

	// 4c. Delimited '${'
	const text3 = "${";
	const res3 = detectToken(text3, 2);
	assert.ok(res3 !== null);
	const candDelimRet = res3.matches.find(m => m.label === "$ret");
	assert.ok(candDelimRet !== undefined);
	assert.equal(simulateReplace(text3, res3, candDelimRet), "${ret}");

	// 4d. Dynamic user variables scanned from script
	const scriptWithVars = "\\set{counter}{0}\n\\pixelrect{icon}{\\row{0xFF0000}}\n$";
	const res4 = detectToken(scriptWithVars, scriptWithVars.length);
	assert.ok(res4 !== null);
	const labels4 = res4.matches.map(m => m.label);
	assert.ok(labels4.includes("$counter"), "Should dynamically detect $counter");
	assert.ok(labels4.includes("$icon"), "Should dynamically detect $icon");
});

// 5. Test mouse buttons inside \click{
test("5. Mouse button candidates inside '\\click{' and click count argument", () => {
	const text = "\\click{";
	const res = detectToken(text, 7);
	assert.ok(res !== null);
	assert.equal(res.context, "Mouse Button");
	assert.equal(res.tokenStart, 7);
	assert.equal(res.tokenEnd, 7);
	const labels = res.matches.map(m => m.label);
	assert.deepEqual(labels, ["L", "R", "M", "B", "F"]);

	const candL = res.matches.find(m => m.label === "L");
	assert.equal(simulateReplace(text, res, candL), "\\click{L");

	// Partial typing \click{l
	const textPartial = "\\click{l";
	const resPartial = detectToken(textPartial, 8);
	assert.ok(resPartial !== null);
	assert.equal(resPartial.tokenStart, 7);
	assert.equal(resPartial.tokenEnd, 8);
	assert.equal(simulateReplace(textPartial, resPartial, candL), "\\click{L");

	// After comma: click count argument
	const textCount = "\\click{L, 2";
	const resCount = detectToken(textCount, textCount.length);
	assert.ok(resCount !== null);
	assert.equal(resCount.context, "Click Count");
	assert.equal(resCount.rawToken, "2");
});

// 6. Test namespaces inside \use{
test("6. Namespace candidates inside '\\use{'", () => {
	const text = "\\use{";
	const res = detectToken(text, 5);
	assert.ok(res !== null);
	assert.equal(res.context, "Namespace");
	assert.equal(res.tokenStart, 5);
	assert.equal(res.tokenEnd, 5);
	const labels = res.matches.map(m => m.label);
	assert.deepEqual(labels, ["sys", "net", "img"]);

	const candSys = res.matches.find(m => m.label === "sys");
	assert.equal(simulateReplace(text, res, candSys), "\\use{sys");

	// Partial typing \use{i
	const textPartial = "\\use{i";
	const resPartial = detectToken(textPartial, 6);
	assert.ok(resPartial !== null);
	assert.equal(resPartial.tokenStart, 5);
	assert.equal(resPartial.tokenEnd, 6);
	const candImg = resPartial.matches.find(m => m.label === "img");
	assert.equal(simulateReplace(textPartial, resPartial, candImg), "\\use{img");
});

// 7. Test extension functions inside \call{
test("7. Extension functions and call argument packs inside '\\call{'", () => {
	const text = "\\call{";
	const res = detectToken(text, 6);
	assert.ok(res !== null);
	assert.equal(res.context, "Extension Call");
	const labels = res.matches.map(m => m.label);
	assert.ok(labels.includes("sys.free"));
	assert.ok(labels.includes("net.send"));
	assert.ok(labels.includes("img.snap"));
	assert.ok(labels.includes("img.at"));
	assert.ok(labels.includes("img.diff"));
	assert.ok(labels.includes("img.watch"));

	const candSnap = res.matches.find(m => m.label === "img.snap");
	assert.equal(simulateReplace(text, res, candSnap), "\\call{img.snap}{0, 0, 100, 100}");

	// Inside argument pack of \call
	const textArgs = "\\call{img.snap}{0, 0, 10, 10";
	const resArgs = detectToken(textArgs, textArgs.length);
	assert.ok(resArgs !== null);
	assert.equal(resArgs.context, "Call Args (img.snap)");
});

// 8. Test key hold combinations inside \kdown{
test("8. Key names and chording inside '\\kdown{'", () => {
	const text = "\\kdown{ctrl+";
	const res = detectToken(text, text.length);
	assert.ok(res !== null);
	assert.equal(res.context, "Key / Modifier");
	assert.equal(res.tokenStart, 12);
	assert.equal(res.tokenEnd, 12);

	const candShift = res.matches.find(m => m.label === "shift");
	assert.equal(simulateReplace(text, res, candShift), "\\kdown{ctrl+shift");

	// Partial key typing \kdown{ctrl+sh
	const textPartial = "\\kdown{ctrl+sh";
	const resPartial = detectToken(textPartial, textPartial.length);
	assert.ok(resPartial !== null);
	assert.equal(resPartial.tokenStart, 12);
	assert.equal(resPartial.tokenEnd, 14);
	assert.equal(simulateReplace(textPartial, resPartial, candShift), "\\kdown{ctrl+shift");
});

// 9. Test conditional branch \if{ condition and comparison operators
test("9. Comparison predicates and logic blocks in '\\if{'", () => {
	// 9a. Directly inside \if{ -> Condition context offering logic blocks and triplet template
	const textDirect = "\\if{ ";
	const resDirect = detectToken(textDirect, 5);
	assert.ok(resDirect !== null);
	assert.equal(resDirect.context, "Predicate Condition");
	const directLabels = resDirect.matches.map(m => m.label);
	assert.ok(directLabels.includes("\\all"));
	assert.ok(directLabels.includes("\\any"));
	assert.ok(directLabels.includes("{}"));

	// 9b. Context filtering: \if{\ strictly filters to predicate combinators (\all, \any) only
	const textSlash = "\\if{\\";
	const resSlash = detectToken(textSlash, textSlash.length);
	assert.ok(resSlash !== null);
	assert.equal(resSlash.context, "Predicate Condition");
	assert.equal(resSlash.rawToken, "\\");
	assert.deepEqual(resSlash.matches.map(m => m.label), ["\\all", "\\any"], "Must ONLY suggest predicate combinators \\all and \\any");

	// Partial typing \if{\al
	const resAl = detectToken("\\if{\\al", 7);
	assert.deepEqual(resAl.matches.map(m => m.label), ["\\all"]);

	// Typing invalid command \if{\s in condition context returns 0 matches (no \set, \sub, etc.)
	const resS = detectToken("\\if{\\s", 6);
	assert.equal(resS.matches.length, 0, "Non-predicate commands like \\set must be excluded from condition context");

	// In contrast, inside a CodeBlock like \\rep{10}{\\, all general commands are available
	const resCodeStream = detectToken("\\rep{10}{\\", 10);
	assert.equal(resCodeStream.context, "Command / Key");
	assert.ok(resCodeStream.matches.length > 50, "Code streams must offer general commands");

	// 9c. Operator position inside triplet \if{ {$x, 
	const textOp = "\\if{ {$x, ";
	const resOp = detectToken(textOp, textOp.length);
	assert.ok(resOp !== null);
	assert.equal(resOp.context, "Predicate Operator");
	assert.equal(resOp.tokenStart, textOp.length);
	assert.equal(resOp.tokenEnd, textOp.length);
	const opLabels = resOp.matches.map(m => m.label);
	assert.deepEqual(opLabels, ["=", "!=", "<", "<=", ">", ">="]);

	const candGte = resOp.matches.find(m => m.label === ">=");
	const replacedOp = simulateReplace(textOp, resOp, candGte);
	assert.equal(replacedOp, "\\if{ {$x, >=");

	// 9d. Partial operator typing \if{ {$x, >
	const textPartialOp = "\\if{ {$x, >";
	const resPartialOp = detectToken(textPartialOp, textPartialOp.length);
	assert.ok(resPartialOp !== null);
	assert.equal(resPartialOp.tokenStart, 10);
	assert.equal(resPartialOp.tokenEnd, 11);
	const replacedPartialOp = simulateReplace(textPartialOp, resPartialOp, candGte);
	assert.equal(replacedPartialOp, "\\if{ {$x, >=");
});

// 10. Test special escape sequences \\, \{, \}, \$
test("10. Special escape sequences '\\\\', '\\{', '\\}', '\\$'", () => {
	for (const esc of ["\\\\", "\\{", "\\}", "\\$"]) {
		const res = detectToken(esc, 2);
		assert.ok(res !== null, `Escape ${esc} should detect`);
		assert.equal(res.context, "Escape Sequence");
		assert.equal(res.tokenStart, 0);
		assert.equal(res.tokenEnd, 2);
		assert.equal(res.matches[0].label, esc);
	}
});

// 11. Test nested scopes and multi-line script stability
test("11. Nested scopes across multiple lines with comments", () => {
	const script = `\\#!syntax: free
\\# Define user macro
\\def{my_click}[0]{
  \\kdown{ctrl}
  \\delay{50ms}
  \\kup{ctrl}
}

\\rep{5}{
  \\click{`;

	const cursor = script.length;
	const res = detectToken(script, cursor);
	assert.ok(res !== null);
	assert.equal(res.context, "Mouse Button");
	const labels = res.matches.map(m => m.label);
	assert.deepEqual(labels, ["L", "R", "M", "B", "F"]);
});

// 12. Whole-document Bracket Balance & Depth detection
test("12. Whole-document Bracket Balance & Depth (checkBracketBalance)", () => {
	// 12a. Balanced script with depth
	const balanced = "\\rep{10}{\n  \\click{L}\n}";
	const cursor1 = balanced.indexOf("{L}") + 2;
	const res1 = checkBracketBalance(balanced, cursor1);
	assert.equal(res1.balanced, true);
	assert.equal(res1.depth, 2);
	assert.equal(res1.statusType, "balanced");

	// 12b. Single unclosed { at line 1
	const singleUnclosed = "\\click{L";
	const resSingle = checkBracketBalance(singleUnclosed);
	assert.equal(resSingle.balanced, false);
	assert.equal(resSingle.statusType, "warning");
	assert.equal(resSingle.unclosedLine, 1);

	// 12c. Multiple unclosed scopes
	const multiUnclosed = "\\rep{10}{\n  \\click{L";
	const resMulti = checkBracketBalance(multiUnclosed);
	assert.equal(resMulti.balanced, false);
	assert.equal(resMulti.statusType, "warning");
	assert.equal(resMulti.firstUnclosedLine, 1);
	assert.equal(resMulti.unclosedCount, 2);

	// 12d. Damaged inner/outer scopes
	const damagedScopes = "\\rep{10}{\n  \\if{ {$x, =, 1} }{\n    \\enter\n  }\n";
	const resDamaged = checkBracketBalance(damagedScopes);
	assert.equal(resDamaged.balanced, false);
	assert.equal(resDamaged.unclosedLine, 1);
	assert.equal(resDamaged.unclosedCount, 1);

	// 12e. Bracket [ mismatch and errors
	const unclosedBracket = "\\wait[100ms";
	const resBracket = checkBracketBalance(unclosedBracket);
	assert.equal(resBracket.balanced, false);
	assert.equal(resBracket.statusType, "warning");

	const mismatchedBracket = "\\guarded[50ms}\n";
	const resMismatch = checkBracketBalance(mismatchedBracket);
	assert.equal(resMismatch.balanced, false);
	assert.equal(resMismatch.statusType, "error");
	assert.equal(resMismatch.statusText, "❌ Unmatched } at line 1");

	// 12f. Unmatched closing } and ]
	const unmatchedClose = "\\click{L}\n}\n";
	const resClose = checkBracketBalance(unmatchedClose);
	assert.equal(resClose.balanced, false);
	assert.equal(resClose.statusType, "error");

	const unmatchedCloseBracket = "\\click{L}]\n";
	const resCloseBracket = checkBracketBalance(unmatchedCloseBracket);
	assert.equal(resCloseBracket.balanced, false);
	assert.equal(resCloseBracket.statusType, "error");

	// 12g. Escaped braces and comments
	const escaped = "\\# comment { and } [\n\\{literal\\}\n\\click{L}";
	const resEsc = checkBracketBalance(escaped, escaped.length);
	assert.equal(resEsc.balanced, true);

	// 12h. Verbatim text block syntax checks
	const textNonEmpty = "\\text{\\invalid{}}\n}\n\\endtext{}";
	const resNonEmpty = checkBracketBalance(textNonEmpty);
	assert.equal(resNonEmpty.balanced, false);
	assert.equal(resNonEmpty.statusType, "error");

	const orphanEndtext = "\\enter\n\\endtext{}";
	const resOrphan = checkBracketBalance(orphanEndtext);
	assert.equal(resOrphan.balanced, false);
	assert.equal(resOrphan.statusType, "error");

	// 12i. Anonymous brace blocks { ... } balance cleanly
	const anonBlock = "{\n  \\click{L}\n}";
	const resAnon = checkBracketBalance(anonBlock, anonBlock.length);
	assert.equal(resAnon.balanced, true);
	assert.equal(resAnon.depth, 0);
});

// 13. Verbatim text block payload immunity in bracket checking
test("13. Verbatim text block payload is immune to bracket balance errors", () => {
	const verbatimScript = "\\#!syntax: free\n\\text{}payload with unclosed { and [ and } and ] here\\endtext{}\n\\enter";
	const res = checkBracketBalance(verbatimScript, 10);
	assert.equal(res.balanced, true);
	assert.equal(res.statusType, "balanced");
});

// 14. Structural Scope Breadcrumb detection (detectStructuralScope)
test("14. Structural Scope Breadcrumb detection across all DSL scopes", () => {
	assert.equal(detectStructuralScope("echo Hello World", 5), "Scope: Document Root");
	assert.equal(detectStructuralScope("\\# this is a single comment line", 10), "Scope: Comment");

	const preambleText = "\\#!syntax: free\n\\click{L}";
	assert.equal(detectStructuralScope(preambleText, 4), "Scope: Layout Preamble");

	const defText = "\\def{my_macro}[1]{\\ctrl+#1\\enter}\n";
	assert.equal(detectStructuralScope(defText, defText.indexOf("my_macro") + 2), "Scope: \\def (name)");
	assert.equal(detectStructuralScope(defText, defText.indexOf("1") + 1), "Scope: \\def [argc]");
	assert.equal(detectStructuralScope(defText, defText.indexOf("ctrl") + 2), "Scope: \\def{my_macro} (Body)");

	const repText = "\\rep{25}{\n  \\delay{50ms}\n}\n";
	assert.equal(detectStructuralScope(repText, repText.indexOf("25") + 1), "Scope: \\rep (count)");
	assert.equal(detectStructuralScope(repText, repText.indexOf("delay") - 1), "Scope: \\rep{25} (Body)");

	const ifText = "\\if{ {$x, >=, 0} }{\n  \\click{L}\n}{\n  \\delay{50ms}\n}\n";
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("{$x")), "Scope: \\if (Condition)");
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("$x")), "Scope: \\if (Condition) > Predicate Triplet");
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("click") - 1), "Scope: \\if (Then-branch)");
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("delay") - 1), "Scope: \\if (Else-branch)");

	const guardText = "\\guarded[50ms]{ {$err, =, 0} }{\n  \\click{L}\n}{\n  \\esc\n}\n";
	assert.equal(detectStructuralScope(guardText, guardText.indexOf("50ms")), "Scope: \\guarded [throttle]");
	assert.equal(detectStructuralScope(guardText, guardText.indexOf("$err")), "Scope: \\guarded (Invariant) > Predicate Triplet");

	const logicText = "\\if{ \\all{ {$x, >=, 0}, {$y, <=, 10} } }{\n  \\enter\n}\n";
	assert.equal(detectStructuralScope(logicText, logicText.indexOf("\\all") + 6), "Scope: \\if (Condition) > \\all (Conjunction)");
	assert.equal(detectStructuralScope(logicText, logicText.indexOf("$x")), "Scope: \\if (Condition) > \\all (Conjunction) > Predicate Triplet");
});

// 15. User Focus Cases: \a{\b{, \aa{\bb{, \if{{1, \if{{1,, \if{{1,=, & Arguments Packs
test("15. Target LSP Cases: \\a{\\b{, \\aa{\\bb{, \\if{{1, \\if{{1,, \\if{{1,=, and Argument Packs", () => {
	// --- Case 1: \a{\b{ (Lone printable key commands followed by literal braces) ---
	const case1 = "\\a{\\b{";
	const resBalance1 = checkBracketBalance(case1);
	assert.equal(resBalance1.balanced, true, "\\a{\\b{ bare braces in text position must not cause imbalance");
	assert.equal(resBalance1.depth, 0);
	assert.equal(detectStructuralScope(case1, case1.length), "Scope: Document Root");
	const resToken1 = detectToken(case1, case1.length);
	assert.equal(resToken1, null, "Literal text position should return null (Text Ready)");

	// Lone key with empty separator \a{}
	const case1Empty = "\\a{}";
	assert.equal(checkBracketBalance(case1Empty).balanced, true);
	assert.equal(checkBracketBalance(case1Empty).depth, 0);

	// --- Case 2: \aa{\bb{ (Multi-letter unknown commands / user macros) ---
	const case2 = "\\aa{\\bb{";
	const resBalance2 = checkBracketBalance(case2);
	assert.equal(resBalance2.balanced, false, "\\aa{\\bb{ has 2 unclosed structural blocks");
	assert.equal(resBalance2.depth, 2);
	assert.equal(resBalance2.statusType, "warning");
	assert.equal(resBalance2.firstUnclosedLine, 1);
	assert.equal(resBalance2.unclosedCount, 2);
	assert.equal(detectStructuralScope(case2, case2.length), "Scope: \\aa > \\bb");

	// Symmetric closure \aa{\bb{}}
	const case2Closed = "\\aa{\\bb{}}";
	const resBalance2Closed = checkBracketBalance(case2Closed);
	assert.equal(resBalance2Closed.balanced, true);
	assert.equal(resBalance2Closed.depth, 0);
	assert.equal(detectStructuralScope(case2Closed, case2Closed.length), "Scope: Document Root");

	// --- Case 3: \if{{1 (Inside predicate comparison triplet LHS) ---
	const case3 = "\\if{{1";
	const resBalance3 = checkBracketBalance(case3);
	assert.equal(resBalance3.balanced, false);
	assert.equal(resBalance3.depth, 2);
	assert.equal(detectStructuralScope(case3, case3.length), "Scope: \\if (Condition) > Predicate Triplet");

	const resToken3 = detectToken(case3, case3.length);
	assert.ok(resToken3 !== null);
	assert.equal(resToken3.context, "Predicate Operand");
	assert.equal(resToken3.rawToken, "1");
	assert.equal(resToken3.tokenStart, 5);
	assert.equal(resToken3.tokenEnd, 6);

	// Fresh LHS before typing 1 (\if{{)
	const case3Fresh = "\\if{{";
	const resToken3Fresh = detectToken(case3Fresh, case3Fresh.length);
	assert.ok(resToken3Fresh !== null);
	assert.equal(resToken3Fresh.context, "Predicate Operand");
	assert.equal(resToken3Fresh.rawToken, "{lhs}");
	assert.ok(resToken3Fresh.matches.some(m => m.label === "$ret"));
	assert.ok(resToken3Fresh.matches.some(m => m.label === "$err"));

	// --- Case 4: \if{{1, (Inside predicate comparison triplet Operator) ---
	const case4 = "\\if{{1,";
	const resBalance4 = checkBracketBalance(case4);
	assert.equal(resBalance4.balanced, false);
	assert.equal(resBalance4.depth, 2);
	assert.equal(detectStructuralScope(case4, case4.length), "Scope: \\if (Condition) > Predicate Triplet");

	const resToken4 = detectToken(case4, case4.length);
	assert.ok(resToken4 !== null);
	assert.equal(resToken4.context, "Predicate Operator");
	assert.equal(resToken4.rawToken, "{op}");
	assert.equal(resToken4.tokenStart, 7);
	assert.equal(resToken4.tokenEnd, 7);
	const opLabels4 = resToken4.matches.map(m => m.label);
	assert.deepEqual(opLabels4, ["=", "!=", "<", "<=", ">", ">="]);

	// --- Case 5: \if{{1,=, (Inside predicate comparison triplet RHS) ---
	const case5 = "\\if{{1,=,";
	const resBalance5 = checkBracketBalance(case5);
	assert.equal(resBalance5.balanced, false);
	assert.equal(resBalance5.depth, 2);
	assert.equal(detectStructuralScope(case5, case5.length), "Scope: \\if (Condition) > Predicate Triplet");

	const resToken5 = detectToken(case5, case5.length);
	assert.ok(resToken5 !== null);
	assert.equal(resToken5.context, "Predicate Operand");
	assert.equal(resToken5.rawToken, "{rhs}");
	assert.equal(resToken5.tokenStart, 9);
	assert.equal(resToken5.tokenEnd, 9);
	assert.ok(resToken5.matches.some(m => m.label === "$ret"));
	assert.ok(resToken5.matches.some(m => m.label === "$timeout"));

	// --- Argument Packs: \click, \move, \rgb, \call, logic blocks, guarded ---
	// 5a. Click count argument
	const resClick = detectToken("\\click{L, 3", 11);
	assert.equal(resClick.context, "Click Count");
	assert.equal(resClick.rawToken, "3");

	// 5b. Move arguments {dx, dy}
	const resMoveDx = detectToken("\\move{50", 8);
	assert.equal(resMoveDx.context, "Move X (dx)");
	const resMoveDy = detectToken("\\move{50, -20", 13);
	assert.equal(resMoveDy.context, "Move Y (dy)");
	assert.equal(detectStructuralScope("\\move{50, -20}", 10), "Scope: \\move > Arg Pack {dx, dy}");

	// 5c. RGB arguments {r, g, b}
	const resRgbR = detectToken("\\rgb{255", 8);
	assert.equal(resRgbR.context, "RGB Red");
	const resRgbG = detectToken("\\rgb{255, 128", 13);
	assert.equal(resRgbG.context, "RGB Green");
	const resRgbB = detectToken("\\rgb{255, 128, 64", 17);
	assert.equal(resRgbB.context, "RGB Blue");
	assert.equal(detectStructuralScope("\\rgb{255, 128, 64}", 12), "Scope: \\rgb > Arg Pack {r, g, b}");

	// 5d. Extension call arguments
	const resCallArgs = detectToken("\\call{img.diff}{$a, $b, 16", 26);
	assert.equal(resCallArgs.context, "Call Args (img.diff)");
	assert.equal(detectStructuralScope("\\call{img.snap}{0, 0, 10, 10}", 20), "Scope: \\call{img.snap} > Arg Pack {x, y, w, h}");

	// 5e. Predicate triplets inside \all, \any, and guarded
	assert.equal(detectToken("\\all{{1,=", 8).context, "Predicate Operator");
	assert.equal(detectToken("\\any{{1,=,", 10).context, "Predicate Operand");
	assert.equal(detectToken("\\guarded[50ms]{{1,=", 19).context, "Predicate Operator");
	assert.equal(detectToken("\\guarded[50ms]{{1,=,", 20).context, "Predicate Operand");
});

// 16. Consumer keys candidates include media_play and media_pause
test("16. Consumer commands candidate dictionary includes media_play and media_pause", () => {
	assert.ok(COMMAND_CANDIDATES.some(c => c.label === "\\media_play"), "Should include \\media_play");
	assert.ok(COMMAND_CANDIDATES.some(c => c.label === "\\media_pause"), "Should include \\media_pause");
});

// 17. Transition Scope and Incomplete Statement Validation
test("17. Transition Scope recognition and Incomplete Statement validation", () => {
	// 17a. \if{{pred}} missing {then}
	const ifIncomplete = "\\if{{$x, =, 0}}";
	const resIfBal = checkBracketBalance(ifIncomplete);
	assert.equal(resIfBal.balanced, false, "Must detect incomplete \\if statement");
	assert.equal(resIfBal.statusType, "warning");
	assert.equal(resIfBal.statusText, "⚠️ Incomplete statement: \\if requires {then}");
	assert.equal(resIfBal.unclosedLine, 1);

	// Scope in transition gap at EOF: \if{{pred}}<cursor>
	const scopeIfGapEOF = detectStructuralScope(ifIncomplete, ifIncomplete.length);
	assert.equal(scopeIfGapEOF, "Scope: \\if (awaiting {then})");

	// Scope in transition gap before {}: \if{{pred}}<cursor>{}
	const ifWithBraces = "\\if{{$x, =, 0}}{}";
	const cursorBeforeThen = ifWithBraces.indexOf("{}");
	const scopeBeforeThen = detectStructuralScope(ifWithBraces, cursorBeforeThen);
	assert.equal(scopeBeforeThen, "Scope: \\if (awaiting {then})");

	// Once cursor is inside {}, scope transitions to Then-branch
	const scopeInsideThen = detectStructuralScope(ifWithBraces, cursorBeforeThen + 1);
	assert.equal(scopeInsideThen, "Scope: \\if (Then-branch)");

	// With {then} closed, \if is balanced
	const resIfComplete = checkBracketBalance(ifWithBraces);
	assert.equal(resIfComplete.balanced, true);
	assert.equal(resIfComplete.statusType, "balanced");

	// 17b. \rep{10} missing {body}
	const repIncomplete = "\\rep{10}";
	const resRepBal = checkBracketBalance(repIncomplete);
	assert.equal(resRepBal.balanced, false, "Must detect incomplete \\rep statement");
	assert.equal(resRepBal.statusType, "warning");
	assert.equal(resRepBal.statusText, "⚠️ Incomplete statement: \\rep requires {body}");
	assert.equal(resRepBal.unclosedLine, 1);

	// Scope in transition gap: \rep{10}<cursor>
	const scopeRepGap = detectStructuralScope(repIncomplete, repIncomplete.length);
	assert.equal(scopeRepGap, "Scope: \\rep (awaiting {body})");

	// 17c. \guarded, \def, \set, \add, \sub, \pixelrect incomplete statements
	assert.equal(checkBracketBalance("\\guarded{{$err, =, 0}}").statusText, "⚠️ Incomplete statement: \\guarded requires {body}");
	assert.equal(checkBracketBalance("\\def{my_macro}").statusText, "⚠️ Incomplete statement: \\def requires {body}");
	assert.equal(checkBracketBalance("\\def{my_macro}[1]").statusText, "⚠️ Incomplete statement: \\def requires {body}");
	assert.equal(checkBracketBalance("\\set{x}").statusText, "⚠️ Incomplete statement: \\set requires {value}");
	assert.equal(checkBracketBalance("\\add{x}").statusText, "⚠️ Incomplete statement: \\add requires {value}");
	assert.equal(checkBracketBalance("\\sub{x}").statusText, "⚠️ Incomplete statement: \\sub requires {value}");
	assert.equal(checkBracketBalance("\\pixelrect{target}").statusText, "⚠️ Incomplete statement: \\pixelrect requires {rows}");

	// 17d. Nested transition scope
	const nestedScript = "\\rep{5}{\n  \\if{{$x, =, 0}}\n}";
	const ifPosInLoop = nestedScript.indexOf("}}") + 2;
	const nestedScope = detectStructuralScope(nestedScript, ifPosInLoop);
	assert.equal(nestedScope, "Scope: \\rep{5} (Body) > \\if (awaiting {then})");
	const nestedBal = checkBracketBalance(nestedScript);
	assert.equal(nestedBal.balanced, false);
	assert.equal(nestedBal.statusText, "⚠️ Incomplete statement: \\if requires {then}");
	assert.equal(nestedBal.unclosedLine, 2);

	// 17e. Completion hint at transition position
	const tokRes = detectToken(ifIncomplete, ifIncomplete.length);
	assert.ok(tokRes !== null, "Token detector should produce completion at transition gap");
	assert.equal(tokRes.context, "Awaiting {then}");
	assert.ok(tokRes.matches.some(m => m.label === "{}" && m.insert.includes("{")));

	// Candidate replacement test at transition cursor
	const cand = tokRes.matches.find(m => m.label === "{}");
	const replaced = simulateReplace(ifIncomplete, tokRes, cand);
	assert.equal(replaced, "\\if{{$x, =, 0}}{\n  \n}");
});

test("18. Verbatim Text Block (\\text{}) suppresses all commands, escapes, and variables, only allowing \\endtext", () => {
	const script = "\\text{}echo \"$var\" \\delay \\endtext{} \\delay{50ms}";
	const openIdx = script.indexOf("\\text{}");
	const endtextIdx = script.indexOf("\\endtext{}");
	const delayOutsideIdx = script.indexOf("\\delay{50ms}");

	// 18a. Scope and verbatim check
	assert.equal(isInsideVerbatim(script, openIdx + 7), true, "Immediately after \\text{} is inside verbatim");
	assert.equal(isInsideVerbatim(script, openIdx + 12), true, "Inside payload is inside verbatim");
	assert.equal(isInsideVerbatim(script, endtextIdx), false, "At \\endtext{} boundary is not inside payload");
	assert.equal(isInsideVerbatim(script, delayOutsideIdx), false, "Outside block is not inside verbatim");

	// 18b. Variable $ inside verbatim: suppressed (returns null)
	const varPos = script.indexOf("$var") + 4;
	const varTok = detectToken(script, varPos);
	assert.equal(varTok, null, "Variables inside verbatim must be suppressed");

	// 18c. Escape \\, \\{ inside verbatim: suppressed
	const escScript = "\\text{}echo \\\\ and \\{ \\endtext{}";
	const slashPos = escScript.indexOf("\\\\") + 2;
	const bracePos = escScript.indexOf("\\{") + 2;
	assert.equal(detectToken(escScript, slashPos), null, "Literal backslash inside verbatim must not prompt escape");
	assert.equal(detectToken(escScript, bracePos), null, "Literal brace inside verbatim must not prompt escape");

	// 18d. Non-terminator backslash command inside verbatim: 0 matches
	const nonTermScript = "\\text{}echo \\delay \\endtext{}";
	const delayPos = nonTermScript.indexOf("\\delay") + 6;
	const delayTok = detectToken(nonTermScript, delayPos);
	assert.ok(delayTok !== null, "Should return detection object for backslash word");
	assert.equal(delayTok.context, "Verbatim Block Terminator");
	assert.equal(delayTok.matches.length, 0, "\\delay inside verbatim must return 0 matches");

	// 18e. Typing backslash \\ alone inside verbatim: only \\endtext is offered
	const slashAloneScript = "\\text{}echo \\";
	const slashAloneTok = detectToken(slashAloneScript, slashAloneScript.length);
	assert.ok(slashAloneTok !== null);
	assert.equal(slashAloneTok.context, "Verbatim Block Terminator");
	assert.equal(slashAloneTok.matches.length, 1, "Only \\endtext candidate should be offered");
	assert.equal(slashAloneTok.matches[0].label, "\\endtext");
	assert.equal(slashAloneTok.matches[0].insert, "\\endtext{}");

	// 18f. Typing \\e inside verbatim: matches \\endtext
	const endtextPartialScript = "\\text{}echo \\e";
	const endtextPartialTok = detectToken(endtextPartialScript, endtextPartialScript.length);
	assert.ok(endtextPartialTok !== null);
	assert.equal(endtextPartialTok.matches.length, 1);
	assert.equal(endtextPartialTok.matches[0].label, "\\endtext");

	// 18g. Outside verbatim: \\delay still matches all regular commands
	const outsideDelayPos = script.indexOf("\\delay{50ms}") + 6;
	const outsideTok = detectToken(script, outsideDelayPos);
	assert.ok(outsideTok !== null);
	assert.ok(outsideTok.matches.some(m => m.label === "\\delay"), "Outside verbatim \\delay must be offered");
});

console.log(`\n=========================================`);
console.log(`ALL ${passed} / ${total} TESTS PASSED SUCCESSFULLY!`);
console.log(`=========================================`);
