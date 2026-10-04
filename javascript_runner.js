/* Separate Node.js worker for SkillNova coding practice. */
"use strict";
const fs = require("fs");
const vm = require("vm");

const MAX_SOURCE_CHARS = 20000;
const MAX_OUTPUT_CHARS = 4096;
const blocked = /\b(require|import|process|globalThis|Function|eval|WebAssembly|child_process|fs|net|http|https)\b|__proto__|constructor/;

function splitArguments(value) {
  const parts = [], startState = { start: 0, depth: 0, quote: null, escaped: false };
  for (let i = 0; i < value.length; i += 1) {
    const char = value[i];
    if (startState.quote) {
      if (startState.escaped) startState.escaped = false;
      else if (char === "\\") startState.escaped = true;
      else if (char === startState.quote) startState.quote = null;
    } else if (char === "'" || char === '"') startState.quote = char;
    else if (char === "[" || char === "{") startState.depth += 1;
    else if (char === "]" || char === "}") startState.depth -= 1;
    else if (char === "," && startState.depth === 0) {
      parts.push(value.slice(startState.start, i).trim()); startState.start = i + 1;
    }
  }
  parts.push(value.slice(startState.start).trim());
  return parts;
}

function parsePiece(piece) {
  if (/^[\[{]/.test(piece) || /^[+-]?(\d+\.?\d*|\.\d+)$/.test(piece)) {
    try { return JSON.parse(piece); } catch (_) { /* keep human-readable input as text */ }
  }
  return piece;
}

function output(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

try {
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  const source = request.code;
  if (typeof source !== "string" || source.length > MAX_SOURCE_CHARS) throw new Error("Source code is missing or exceeds the 20,000 character limit.");
  if (blocked.test(source)) throw new Error("This JavaScript feature is not allowed in the local runner.");
  const match = source.match(/(?:^|\n)\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/);
  if (!match) throw new Error("Define a named JavaScript function; the runner calls the first one you define.");
  const logs = [];
  const sandbox = { console: { log: (...values) => logs.push(values.map(output).join(" ")) } };
  vm.createContext(sandbox, { codeGeneration: { strings: false, wasm: false } });
  vm.runInContext(source, sandbox, { timeout: 1500 });
  const fn = sandbox[match[1]];
  if (typeof fn !== "function") throw new Error("Could not find the submitted function.");
  const result = fn(...splitArguments(String(request.input || "")).map(parsePiece));
  if (result && typeof result.then === "function") throw new Error("Async functions are not supported.");
  const rendered = result === undefined ? logs.join("\n") : output(result);
  if (!rendered) throw new Error("Your function returned no value. Return the answer for each test case.");
  if (rendered.length > MAX_OUTPUT_CHARS) throw new Error("Output limit exceeded.");
  process.stdout.write(JSON.stringify({ ok: true, output: rendered }));
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, error: `${error.name}: ${error.message}`.slice(0, 500) }));
}
