import assert from "node:assert/strict";
import { formatMacroCode } from "../src/modules/formatter.ts";

console.log("=========================================");
console.log("Running Formatter Verification Suite");
console.log("=========================================");

// 1. Target benchmark case: Typewriter mixed code homomorphically converted to free layout
const typewriterSample = `\\def{press}[1]{\\kdown{#1}\\delay{20ms}\\kup{#1}}
\\press{enter}
echo hello`;

const formatted1 = formatMacroCode(typewriterSample);
console.log("Formatted output 1:\n" + formatted1);

assert.ok(formatted1.startsWith("\\#!syntax: free"), "Must begin with \\#!syntax: free");
assert.ok(formatted1.includes("  \\kdown{#1}\n  \\delay{20ms}\n  \\kup{#1}"), "Must indent \\def body with 2 spaces");
assert.ok(formatted1.includes("\\text{}echo hello\\endtext{}"), "Must consolidate typing into \\text{}...\\endtext{}");

// 2. Verbatim block preservation
const verbatimSample = `\\#!syntax: free
\\text{}literal with { and [ and special \\chars\\endtext{}
\\enter`;

const formatted2 = formatMacroCode(verbatimSample);
assert.ok(formatted2.includes("\\text{}literal with { and [ and special \\chars\\endtext{}"), "Verbatim block must be preserved 100% intact");

// 3. Nested guarded and if blocks
const nestedSample = `\\#!syntax: free
\\guarded[50ms]{ {$err, =, 0} }{
\\if{ {$x, >, 0} }{
\\click{L}
\\delay{50ms}
}{
\\esc
}
}{
\\tap{h}
}`;

const formatted3 = formatMacroCode(nestedSample);
console.log("Formatted output 3:\n" + formatted3);
assert.ok(formatted3.includes("  \\if{ {$x, >, 0} }{"), "Must indent nested \\if by 2 spaces");
assert.ok(formatted3.includes("    \\click{L}"), "Must indent inner \\click by 4 spaces");
assert.ok(formatted3.includes("  \\tap{h}"), "Must indent abort handler by 2 spaces");

console.log("\n=========================================");
console.log("ALL FORMATTER TESTS PASSED SUCCESSFULLY!");
console.log("=========================================");
