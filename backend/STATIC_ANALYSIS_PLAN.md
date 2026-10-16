# CodeAssist Static Analysis Extension

## Rules
- Existing Python `ast` pipeline (graph, BFS, cycle-guard, Redis cache, Qdrant, WebSocket flow) stays unchanged and must keep working after every stage.
- New code goes behind a `LanguageAdapter` interface; the rest of CodeAssist never cares about language.
- C/C++ parsing uses tree-sitter (`tree-sitter-c`, `tree-sitter-cpp`). No Clang/LLVM dependency in the backend (Docker 500MB limit).
- Analyses (dominators, dataflow, slicing) are written once and shared. Only CFG builders are per-language.
- Implement one stage at a time. Stop after each stage and wait.

## Stage 1: Language Adapter + tree-sitter C/C++
- `LanguageAdapter` methods:
  - `extract_functions(file)` -> qualified name, signature, span, code
  - `extract_calls(function)` -> callee names
  - `extract_loose_chunks(file)` -> globals, macros, structs
  - `normalize_for_hash(function)` -> whitespace/comment-free tokens for `ast_hash`
- Wrap current Python code as `PythonAdapter` (no behavior change). Add `CAdapter`, `CppAdapter`.
- Cache key = qualified name + parameter signature (handles C++ overloads).
- Call resolution: project-wide symbol index; `static` = file-local; ambiguous names add all candidates; function pointers/virtual calls marked unresolved.
- `#define`s are loose chunks; templates indexed once, no instantiation.

## Stage 2: CFG and Basic Blocks (Python + C/C++)
- One shared `CFG` / `BasicBlock` / `Edge` model; two builders (Python `ast`, tree-sitter).
- Edge types: Fallthrough, BranchTrue, BranchFalse, LoopBack, Break, Continue, Return.
- Python: if/elif/else, for/while (+ loop else), break/continue, return, try/except/finally, with, and/or short-circuit.
- C/C++: if, for, while, do-while, switch, break/continue, goto, return, &&/||, ternary.
- Detect unreachable blocks (no path from Entry).

## Stage 3: Dataflow + Dominators + Phi
- Generic worklist fixed-point engine (direction, join, transfer as parameters).
- Live variables (backward, may): IN[B] = GEN[B] ∪ (OUT[B] \ KILL[B]); OUT[B] = ∪ IN[succ].
- Reaching definitions (forward, may) with def-use chains.
- Dominator tree (iterative), dominance frontiers, phi placement per variable.
- Findings: dead stores, unused vars, maybe-uninitialized (non-param var live at entry).

## Stage 4: Error Log -> Block -> Backward Slice -> Prompt
- Parse compiler errors and Python tracebacks into file, line, col, error_type, symbol.
- Map line to function and basic block via source spans.
- Backward slice from failing statement: data deps (reaching defs) + control deps (guarding branches).
- Add slice and static findings to the final prompt alongside line+function chunk, intent, graph summary, Qdrant hypotheses.
- When a traceback exists, use it as a deterministic anchor instead of LLM-parsed function names.

## Evaluation
- Python only: Defectors-based set, with vs without slice (localization accuracy, token count).

## Known Limitations (accepted)
- C/C++ Qdrant collection is empty: skip the hypothesis step when empty.
- CodeBERT is weaker on C/C++: no change.
- Function pointers, virtual dispatch, macros: mark call as unresolved, tell the LLM, don't resolve.
- CFG treats calls as plain statements and C++ try/catch as straight-line code.
- Wrap CFG building and slicing in try/except; on failure fall back to the existing anchor function + line chunk.

## Cut order if short on time
Phi placement, then C++ extras. Never cut dominators or the slicing stage.
