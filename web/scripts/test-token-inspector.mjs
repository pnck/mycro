#!/usr/bin/env node
import assert from "node:assert/strict";
import {
	detectToken,
	checkBracketBalance,
	detectStructuralScope,
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

// 2. Test \# at start of line
test("2. Line prefix '\\#' matches comment and ALL syntax directives", () => {
	const text = "\\#";
	const res = detectToken(text, 2);
	assert.ok(res !== null, "Result should not be null");
	assert.equal(res.context, "Comment / Directive");
	assert.equal(res.rawToken, "\\#");
	assert.equal(res.tokenStart, 0);
	assert.equal(res.tokenEnd, 2);

	const labels = res.matches.map(m => m.label);
	assert.ok(labels.includes("\\#"), "Should include comment \\#");
	assert.ok(labels.includes("\\#!syntax: free"), "Should include \\#!syntax: free");
	assert.ok(labels.includes("\\#!syntax: typewriter"), "Should include \\#!syntax: typewriter");
	assert.ok(labels.includes("\\#!syntax: newline=ignore, space=emit"), "Should include fine-grained syntax");

	// Verify replacement has NO duplicate prefix
	const candFree = res.matches.find(m => m.label === "\\#!syntax: free");
	const replaced = simulateReplace(text, res, candFree);
	assert.equal(replaced, "\\#!syntax: free\n", "Should cleanly replace \\# with \\#!syntax: free\\n without duplicate backslash/hash");

	const candComment = res.matches.find(m => m.label === "\\#");
	const replacedComment = simulateReplace(text, res, candComment);
	assert.equal(replacedComment, "\\# ", "Should cleanly replace \\# with \\# ");
});

// 3. Test \#!
test("3. Line prefix '\\#!' matches all syntax directives", () => {
	const text = "\\#!";
	const res = detectToken(text, 3);
	assert.ok(res !== null, "Result should not be null");
	assert.equal(res.context, "Syntax Directive");
	assert.equal(res.rawToken, "\\#!");
	assert.equal(res.tokenStart, 0);
	assert.equal(res.tokenEnd, 3);

	const labels = res.matches.map(m => m.label);
	assert.ok(labels.includes("\\#!syntax: free"));
	assert.ok(labels.includes("\\#!syntax: typewriter"));
	assert.ok(labels.includes("\\#!syntax: newline=ignore, space=emit"));
	assert.ok(labels.includes("\\#!syntax: space=emit"));

	const cand = res.matches.find(m => m.label === "\\#!syntax: typewriter");
	const replaced = simulateReplace(text, res, cand);
	assert.equal(replaced, "\\#!syntax: typewriter\n", "Should cleanly replace \\#! with \\#!syntax: typewriter\\n");
});

// 4. Test \#!syn
test("4. Line prefix '\\#!syn' matches all syntax directives with accurate range", () => {
	const text = "\\#!syn";
	const res = detectToken(text, 6);
	assert.ok(res !== null, "Result should not be null");
	assert.equal(res.context, "Syntax Directive");
	assert.equal(res.rawToken, "\\#!syn");
	assert.equal(res.tokenStart, 0);
	assert.equal(res.tokenEnd, 6);

	const labels = res.matches.map(m => m.label);
	assert.ok(labels.length >= 8, `Expected all 8 syntax options, got ${labels.length}`);
	assert.ok(labels.includes("\\#!syntax: free"));
	assert.ok(labels.includes("\\#!syntax: newline=ignore, space=emit"));

	const cand = res.matches.find(m => m.label === "\\#!syntax: free");
	const replaced = simulateReplace(text, res, cand);
	assert.equal(replaced, "\\#!syntax: free\n", "Should cleanly replace \\#!syn with \\#!syntax: free\\n");
});

// 5. Test \#!syntax: free filtering
test("5. Fine-grained '\\#!syntax: n' filters to newline directives", () => {
	const text = "\\#!syntax: n";
	const res = detectToken(text, 12);
	assert.ok(res !== null);
	assert.equal(res.tokenStart, 0);
	assert.equal(res.tokenEnd, 12);
	const labels = res.matches.map(m => m.label);
	assert.ok(labels.includes("\\#!syntax: newline=ignore, space=emit"));
	assert.ok(labels.includes("\\#!syntax: newline=emit, space=ignore"));
	assert.ok(!labels.includes("\\#!syntax: free"));
});

// 6. Test \set
test("6. Command '\\set' matches \\set accurately and replaces without duplicate prefix", () => {
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

// 7. Test variable reference $ and $ret
test("7. Variable reference '$' and '$ret'", () => {
	// 7a. Lone '$'
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

	// 7b. '$ret'
	const text2 = "$ret";
	const res2 = detectToken(text2, 4);
	assert.ok(res2 !== null);
	assert.equal(res2.tokenStart, 0);
	assert.equal(res2.tokenEnd, 4);
	const candRet = res2.matches.find(m => m.label === "$ret");
	assert.ok(candRet !== undefined);
	const replacedRet = simulateReplace(text2, res2, candRet);
	assert.equal(replacedRet, "$ret");

	// 7c. Delimited '${'
	const text3 = "${";
	const res3 = detectToken(text3, 2);
	assert.ok(res3 !== null);
	assert.equal(res3.tokenStart, 0);
	assert.equal(res3.tokenEnd, 2);
	const candDelimRet = res3.matches.find(m => m.label === "$ret");
	assert.ok(candDelimRet !== undefined);
	const replacedDelim = simulateReplace(text3, res3, candDelimRet);
	assert.equal(replacedDelim, "${ret}", "Delimited form should replace with ${ret}");

	// 7d. Dynamic user variables scanned from script
	const scriptWithVars = "\\set{counter}{0}\n\\pixelrect{icon}{\\row{0xFF0000}}\n$";
	const res4 = detectToken(scriptWithVars, scriptWithVars.length);
	assert.ok(res4 !== null);
	const labels4 = res4.matches.map(m => m.label);
	assert.ok(labels4.includes("$counter"), "Should dynamically detect $counter");
	assert.ok(labels4.includes("$icon"), "Should dynamically detect $icon");
});

// 8. Test mouse buttons inside \click{
test("8. Mouse button candidates inside '\\click{'", () => {
	const text = "\\click{";
	const res = detectToken(text, 7);
	assert.ok(res !== null);
	assert.equal(res.context, "Mouse Button");
	assert.equal(res.tokenStart, 7);
	assert.equal(res.tokenEnd, 7);
	const labels = res.matches.map(m => m.label);
	assert.deepEqual(labels, ["L", "R", "M", "B", "F"]);

	const candL = res.matches.find(m => m.label === "L");
	const replaced = simulateReplace(text, res, candL);
	assert.equal(replaced, "\\click{L");

	// When typing partial button name \click{l
	const textPartial = "\\click{l";
	const resPartial = detectToken(textPartial, 8);
	assert.ok(resPartial !== null);
	assert.equal(resPartial.tokenStart, 7);
	assert.equal(resPartial.tokenEnd, 8);
	const replacedPartial = simulateReplace(textPartial, resPartial, candL);
	assert.equal(replacedPartial, "\\click{L");
});

// 9. Test namespaces inside \use{
test("9. Namespace candidates inside '\\use{'", () => {
	const text = "\\use{";
	const res = detectToken(text, 5);
	assert.ok(res !== null);
	assert.equal(res.context, "Namespace");
	assert.equal(res.tokenStart, 5);
	assert.equal(res.tokenEnd, 5);
	const labels = res.matches.map(m => m.label);
	assert.deepEqual(labels, ["sys", "net", "img"]);

	const candSys = res.matches.find(m => m.label === "sys");
	const replaced = simulateReplace(text, res, candSys);
	assert.equal(replaced, "\\use{sys");

	// Partial typing \use{i
	const textPartial = "\\use{i";
	const resPartial = detectToken(textPartial, 6);
	assert.ok(resPartial !== null);
	assert.equal(resPartial.tokenStart, 5);
	assert.equal(resPartial.tokenEnd, 6);
	const candImg = resPartial.matches.find(m => m.label === "img");
	const replacedImg = simulateReplace(textPartial, resPartial, candImg);
	assert.equal(replacedImg, "\\use{img");
});

// 10. Test extension functions inside \call{
test("10. Extension functions inside '\\call{'", () => {
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
	const replaced = simulateReplace(text, res, candSnap);
	assert.equal(replaced, "\\call{img.snap}{0, 0, 100, 100}");
});

// 11. Test key hold combinations inside \kdown{
test("11. Key names and chording inside '\\kdown{'", () => {
	const text = "\\kdown{ctrl+";
	const res = detectToken(text, text.length);
	assert.ok(res !== null);
	assert.equal(res.context, "Key / Modifier");
	assert.equal(res.tokenStart, 12);
	assert.equal(res.tokenEnd, 12);

	const candShift = res.matches.find(m => m.label === "shift");
	const replaced = simulateReplace(text, res, candShift);
	assert.equal(replaced, "\\kdown{ctrl+shift");

	// Partial key typing \kdown{ctrl+sh
	const textPartial = "\\kdown{ctrl+sh";
	const resPartial = detectToken(textPartial, textPartial.length);
	assert.ok(resPartial !== null);
	assert.equal(resPartial.tokenStart, 12);
	assert.equal(resPartial.tokenEnd, 14);
	const replacedPartial = simulateReplace(textPartial, resPartial, candShift);
	assert.equal(replacedPartial, "\\kdown{ctrl+shift");
});

// 12. Test conditional branch \if{ condition and comparison operators
test("12. Comparison predicates and logic blocks in '\\if{'", () => {
	// 12a. Directly inside \if{
	const textDirect = "\\if{ ";
	const resDirect = detectToken(textDirect, 5);
	assert.ok(resDirect !== null);
	assert.equal(resDirect.context, "Predicate Condition");
	const directLabels = resDirect.matches.map(m => m.label);
	assert.ok(directLabels.includes("\\all"));
	assert.ok(directLabels.includes("\\any"));
	assert.ok(directLabels.includes("="));
	assert.ok(directLabels.includes("!="));
	assert.ok(directLabels.includes(">="));

	// 12b. Operator position inside triplet \if{ {$x, 
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

	// 12c. Partial operator typing \if{ {$x, >
	const textPartialOp = "\\if{ {$x, >";
	const resPartialOp = detectToken(textPartialOp, textPartialOp.length);
	assert.ok(resPartialOp !== null);
	assert.equal(resPartialOp.tokenStart, 10);
	assert.equal(resPartialOp.tokenEnd, 11);
	const replacedPartialOp = simulateReplace(textPartialOp, resPartialOp, candGte);
	assert.equal(replacedPartialOp, "\\if{ {$x, >=");
});

// 13. Test special escape sequences \\, \{, \}, \$
test("13. Special escape sequences '\\\\', '\\{', '\\}', '\\$'", () => {
	for (const esc of ["\\\\", "\\{", "\\}", "\\$"]) {
		const res = detectToken(esc, 2);
		assert.ok(res !== null, `Escape ${esc} should detect`);
		assert.equal(res.context, "Escape Sequence");
		assert.equal(res.tokenStart, 0);
		assert.equal(res.tokenEnd, 2);
		assert.equal(res.matches[0].label, esc);
	}
});

// 14. Test nested scopes and multi-line script stability
test("14. Nested scopes across multiple lines with comments", () => {
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

// 15. Whole-document Bracket Balance & Depth detection
test("15. Whole-document Bracket Balance & Depth (checkBracketBalance)", () => {
	// 15a. Balanced script with depth
	const balanced = "\\rep{10}{\n  \\click{L}\n}";
	// cursor inside click
	const cursor1 = balanced.indexOf("{L}") + 2;
	const res1 = checkBracketBalance(balanced, cursor1);
	assert.equal(res1.balanced, true);
	assert.equal(res1.depth, 2);
	assert.equal(res1.statusType, "balanced");
	assert.equal(res1.statusText, "✓ Braces: Balanced (depth 2)");

	// 15b. Single unclosed { at line 1
	const singleUnclosed = "\\click{L";
	const resSingle = checkBracketBalance(singleUnclosed);
	assert.equal(resSingle.balanced, false);
	assert.equal(resSingle.statusType, "warning");
	assert.equal(resSingle.unclosedLine, 1);
	assert.equal(resSingle.statusText, "⚠️ Unclosed { at line 1 (\\click (btn))");

	// 15c. Multiple unclosed scopes: reports root/first unclosed scope at line 1
	const multiUnclosed = "\\rep{10}{\n  \\click{L";
	const resMulti = checkBracketBalance(multiUnclosed);
	assert.equal(resMulti.balanced, false);
	assert.equal(resMulti.statusType, "warning");
	assert.equal(resMulti.firstUnclosedLine, 1);
	assert.equal(resMulti.unclosedCount, 2);
	assert.equal(resMulti.statusText, "⚠️ First unclosed { at line 1 (\\rep (Body)) (2 unclosed in total)");

	// 15d. Two damaged scopes: inner closed, outer damaged
	const damagedScopes = "\\rep{10}{\n  \\if{ {$x, =, 1} }{\n    \\enter\n  }\n";
	const resDamaged = checkBracketBalance(damagedScopes);
	assert.equal(resDamaged.balanced, false);
	assert.equal(resDamaged.unclosedLine, 1);
	assert.equal(resDamaged.unclosedCount, 1);
	assert.equal(resDamaged.statusText, "⚠️ Unclosed { at line 1 (\\rep (Body))");

	// 15e. Unclosed [ at line 1
	const unclosedBracket = "\\guarded[50ms{\n  \\click{L}\n}";
	const res3 = checkBracketBalance(unclosedBracket);
	assert.equal(res3.balanced, false);
	assert.equal(res3.statusType, "warning");
	assert.equal(res3.unclosedLine, 1);
	assert.ok(res3.statusText.includes("line 1"));

	// Explicit unclosed [
	const unclosedBracket2 = "\\wait[100ms";
	const res3b = checkBracketBalance(unclosedBracket2);
	assert.equal(res3b.balanced, false);
	assert.equal(res3b.statusType, "warning");
	assert.equal(res3b.unclosedLine, 1);
	assert.ok(res3b.statusText.includes("line 1"));

	// Mismatched closing } when [ expected
	const mismatchedBracket = "\\guarded[50ms}\n";
	const res3c = checkBracketBalance(mismatchedBracket);
	assert.equal(res3c.balanced, false);
	assert.equal(res3c.statusType, "error");
	assert.equal(res3c.statusText, "❌ Unmatched } at line 1");

	// 15f. Unmatched closing } at line 2
	const unmatchedClose = "\\click{L}\n}\n";
	const res4 = checkBracketBalance(unmatchedClose);
	assert.equal(res4.balanced, false);
	assert.equal(res4.statusType, "error");
	assert.equal(res4.statusText, "❌ Unmatched } at line 2");

	// 15g. Unmatched closing ] at line 1
	const unmatchedCloseBracket = "\\click{L}]\n";
	const res5 = checkBracketBalance(unmatchedCloseBracket);
	assert.equal(res5.balanced, false);
	assert.equal(res5.statusType, "error");
	assert.equal(res5.statusText, "❌ Unmatched ] at line 1");

	// 15h. Escaped braces and comments do not trigger imbalance
	const escaped = "\\# this is a comment with { and } and [\n\\{literal\\}\n\\click{L}";
	const res6 = checkBracketBalance(escaped, escaped.length);
	assert.equal(res6.balanced, true);
	assert.equal(res6.statusType, "balanced");

	// 15i. Unclosed verbatim text block
	const unclosedVerbatim = "\\rep{10}{\n  \\text{} unclosed text block without trailer\n";
	const resVerb = checkBracketBalance(unclosedVerbatim);
	assert.equal(resVerb.balanced, false);
	assert.equal(resVerb.firstUnclosedLine, 1);
	assert.equal(resVerb.unclosedCount, 2);

	// 15j. \text with non-empty braces (user case \text{\invalid{}}\n}\n\endtext{})
	const textNonEmpty = "\\text{\\invalid{}}\n}\n\\endtext{}";
	const resNonEmpty = checkBracketBalance(textNonEmpty);
	assert.equal(resNonEmpty.balanced, false);
	assert.equal(resNonEmpty.statusType, "error");
	assert.equal(resNonEmpty.unmatchedLine, 1);
	assert.equal(resNonEmpty.statusText, "❌ \\text requires {} at line 1");

	// 15k. \endtext{} without active text block
	const orphanEndtext = "\\enter\n\\endtext{}";
	const resOrphan = checkBracketBalance(orphanEndtext);
	assert.equal(resOrphan.balanced, false);
	assert.equal(resOrphan.statusType, "error");
	assert.equal(resOrphan.unmatchedLine, 2);
	assert.equal(resOrphan.statusText, "❌ endtext without text block at line 2");

	// 15l. Multi-letter unknown commands / macro calls: \aa{\bb{ has depth 2, symmetric closure
	const multiCmds = "\\aa{\\bb{";
	const resMultiCmds = checkBracketBalance(multiCmds);
	assert.equal(resMultiCmds.balanced, false);
	assert.equal(resMultiCmds.depth, 2);
	assert.equal(resMultiCmds.statusType, "warning");
	assert.equal(resMultiCmds.firstUnclosedLine, 1);
	assert.equal(resMultiCmds.unclosedCount, 2);
	assert.equal(detectStructuralScope(multiCmds, multiCmds.length), "Scope: \\aa > \\bb");

	// 15m. Closing inner brace in \aa{\bb{|} reduces depth to 1 without triggering "Unmatched }"
	const multiCmdsClosedInner = "\\aa{\\bb{}";
	const resClosedInner = checkBracketBalance(multiCmdsClosedInner, multiCmdsClosedInner.length - 1);
	assert.equal(resClosedInner.balanced, false);
	assert.equal(resClosedInner.depth, 2); // depth at cursor inside \bb{}
	assert.equal(resClosedInner.statusType, "warning");
	assert.equal(resClosedInner.statusText, "⚠️ Unclosed { at line 1 (\\aa)");
	assert.equal(detectStructuralScope(multiCmdsClosedInner, multiCmdsClosedInner.length - 1), "Scope: \\aa > \\bb");

	// Fully closed \aa{\bb{}}
	const multiCmdsBalanced = "\\aa{\\bb{}}";
	const resMultiBalanced = checkBracketBalance(multiCmdsBalanced, multiCmdsBalanced.length);
	assert.equal(resMultiBalanced.balanced, true);
	assert.equal(resMultiBalanced.depth, 0);
	assert.equal(resMultiBalanced.statusType, "balanced");
	assert.equal(detectStructuralScope(multiCmdsBalanced, multiCmdsBalanced.length), "Scope: Document Root");

	// 15n. Lone keys with braces \a{\b{ and empty separator \a{} (spec/dsl.md §2 & §5.1)
	const loneKeysWithBraces = "\\a{\\b{ ";
	const resLone = checkBracketBalance(loneKeysWithBraces);
	assert.equal(resLone.balanced, true);
	assert.equal(resLone.depth, 0);
	assert.equal(resLone.statusType, "balanced");
	assert.equal(detectStructuralScope(loneKeysWithBraces, loneKeysWithBraces.length), "Scope: Document Root");

	// Empty {} separator after key command (\a{})
	const keyWithEmptyBraces = "\\a{}";
	const resEmpty = checkBracketBalance(keyWithEmptyBraces, 3); // cursor inside \a{|}
	assert.equal(resEmpty.balanced, true);
	assert.equal(resEmpty.depth, 0);
	assert.equal(resEmpty.statusType, "balanced");
	assert.equal(detectStructuralScope(keyWithEmptyBraces, 3), "Scope: Document Root");

	// 15o. Anonymous brace blocks { ... } balance cleanly
	const anonBlock = "{\n  \\click{L}\n}";
	const resAnon = checkBracketBalance(anonBlock, anonBlock.length);
	assert.equal(resAnon.balanced, true);
	assert.equal(resAnon.depth, 0);
	assert.equal(resAnon.statusType, "balanced");
});

// 16. Verbatim text block payload immunity in bracket checking
test("16. Verbatim text block payload is immune to bracket balance errors", () => {
	const verbatimScript = "\\#!syntax: free\n\\text{}payload with unclosed { and [ and } and ] here\\endtext{}\n\\enter";
	const res = checkBracketBalance(verbatimScript, 10);
	assert.equal(res.balanced, true);
	assert.equal(res.statusType, "balanced");
});

// 17. Structural Scope Breadcrumb detection (detectStructuralScope)
test("17. Structural Scope Breadcrumb detection across all DSL scopes", () => {
	// 17a. Document Root & Comments
	assert.equal(detectStructuralScope("echo Hello World", 5), "Scope: Document Root");
	assert.equal(detectStructuralScope("\\# this is a single comment line", 10), "Scope: Comment");

	// 17b. Layout Preamble
	const preambleText = "\\#!syntax: free\n\\click{L}";
	assert.equal(detectStructuralScope(preambleText, 4), "Scope: Layout Preamble");

	// 17c. Verbatim Text
	const textBlock = "\\#!syntax: free\n\\text{}verbatim echo\\endtext{}\n\\enter";
	const textCursor = textBlock.indexOf("verbatim") + 2;
	assert.equal(detectStructuralScope(textBlock, textCursor), "Scope: Verbatim Text (\\text)");

	// 17d. Macro definition
	const defText = "\\def{my_macro}[1]{\\ctrl+#1\\enter}\n";
	assert.equal(detectStructuralScope(defText, defText.indexOf("my_macro") + 2), "Scope: \\def (name)");
	assert.equal(detectStructuralScope(defText, defText.indexOf("1") + 1), "Scope: \\def [argc]");
	assert.equal(detectStructuralScope(defText, defText.indexOf("ctrl") + 2), "Scope: \\def{my_macro} (Body)");

	// 17e. Loop body & count
	const repText = "\\rep{25}{\n  \\delay{50ms}\n}\n";
	assert.equal(detectStructuralScope(repText, repText.indexOf("25") + 1), "Scope: \\rep (count)");
	assert.equal(detectStructuralScope(repText, repText.indexOf("delay") - 1), "Scope: \\rep{25} (Body)");

	// 17f. Conditional branches & Predicate Triplet
	const ifText = "\\if{ {$x, >=, 0} }{\n  \\click{L}\n}{\n  \\delay{50ms}\n}\n";
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("{$x")), "Scope: \\if (Condition)");
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("$x")), "Scope: \\if (Condition) > Predicate Triplet");
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("click") - 1), "Scope: \\if (Then-branch)");
	assert.equal(detectStructuralScope(ifText, ifText.indexOf("delay") - 1), "Scope: \\if (Else-branch)");

	// 17g. Guarded execution
	const guardText = "\\guarded[50ms]{ {$err, =, 0} }{\n  \\click{L}\n}{\n  \\esc\n}\n";
	assert.equal(detectStructuralScope(guardText, guardText.indexOf("50ms")), "Scope: \\guarded [throttle]");
	assert.equal(detectStructuralScope(guardText, guardText.indexOf("$err")), "Scope: \\guarded (Invariant) > Predicate Triplet");
	assert.equal(detectStructuralScope(guardText, guardText.indexOf("click") - 1), "Scope: \\guarded (Body)");
	assert.equal(detectStructuralScope(guardText, guardText.indexOf("esc") - 1), "Scope: \\guarded (Abort-handler)");

	// 17h. Conjunction & Disjunction Logic Blocks
	const logicText = "\\if{ \\all{ {$x, >=, 0}, {$y, <=, 10} } }{\n  \\enter\n}\n";
	assert.equal(detectStructuralScope(logicText, logicText.indexOf("\\all") + 6), "Scope: \\if (Condition) > \\all (Conjunction)");
	assert.equal(detectStructuralScope(logicText, logicText.indexOf("$x")), "Scope: \\if (Condition) > \\all (Conjunction) > Predicate Triplet");
	assert.equal(detectStructuralScope(logicText, logicText.indexOf("$y")), "Scope: \\if (Condition) > \\all (Conjunction) > Predicate Triplet");

	// 17i. Argument Packs & Parameter scopes
	assert.equal(detectStructuralScope("\\click{L}", 8), "Scope: \\click (btn)");
	assert.equal(detectStructuralScope("\\move{100, -50}", 8), "Scope: \\move > Arg Pack {dx, dy}");
	assert.equal(detectStructuralScope("\\rgb{255, 0, 0}", 7), "Scope: \\rgb > Arg Pack {r, g, b}");
	assert.equal(detectStructuralScope("\\set{x}{10}", 6), "Scope: \\set (var)");
	assert.equal(detectStructuralScope("\\set{x}{10}", 9), "Scope: \\set (val)");
	assert.equal(detectStructuralScope("\\add{x}{1}", 6), "Scope: \\add (var)");
	assert.equal(detectStructuralScope("\\add{x}{1}", 9), "Scope: \\add (val)");
	assert.equal(detectStructuralScope("\\sub{x}{1}", 6), "Scope: \\sub (var)");
	assert.equal(detectStructuralScope("\\sub{x}{1}", 9), "Scope: \\sub (val)");
	assert.equal(detectStructuralScope("\\typenum{$x}", 10), "Scope: \\typenum (ref)");
	assert.equal(detectStructuralScope("\\wait[5s]{$slot.net.msg.val}", 7), "Scope: \\wait [timeout]");
	assert.equal(detectStructuralScope("\\wait[5s]{$slot.net.msg.val}", 12), "Scope: \\wait (slot)");
	assert.equal(detectStructuralScope("\\use{img}", 6), "Scope: \\use (ns)");
	assert.equal(detectStructuralScope("\\call{img.snap}{0, 0, 10, 10}", 8), "Scope: \\call (target)");
	assert.equal(detectStructuralScope("\\call{img.snap}{0, 0, 10, 10}", 18), "Scope: \\call{img.snap} > Arg Pack {x, y, w, h}");
	assert.equal(detectStructuralScope("\\pixelrect{icon}{\\row{0xFF0000}}", 25), "Scope: \\pixelrect{icon} > \\row > Pixel Pack");

	// 17j. Deep nested breadcrumb hierarchy
	const deepScript = "\\rep{10}{\n  \\if{ {$x, =, 1} }{\n    \\call{img.snap}{0, 0, 50, 50}\n  }\n}";
	assert.equal(detectStructuralScope(deepScript, deepScript.indexOf("$x")), "Scope: \\rep{10} > \\if (Condition) > Predicate Triplet");
	assert.equal(detectStructuralScope(deepScript, deepScript.indexOf("50, 50")), "Scope: \\rep{10} > \\if (Then-branch) > \\call{img.snap} > Arg Pack {x, y, w, h}");
});

// 18. Consumer keys candidates include media_play and media_pause
test("18. Consumer commands candidate dictionary includes media_play and media_pause", () => {
	assert.ok(COMMAND_CANDIDATES.some(c => c.label === "\\media_play"), "Should include \\media_play");
	assert.ok(COMMAND_CANDIDATES.some(c => c.label === "\\media_pause"), "Should include \\media_pause");
});

console.log(`\n=========================================`);
console.log(`ALL ${passed} / ${total} TESTS PASSED SUCCESSFULLY!`);
console.log(`=========================================`);
