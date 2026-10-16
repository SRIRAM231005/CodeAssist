import json
import re
from typing import Optional
import google.generativeai as genai


class LLMAnalyzer:
    """
    LLM acts as both analyzer AND navigator.
    Returns structured JSON telling the router what to do next.
    """

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model = genai.GenerativeModel(model_name)

    def parse_query(self, user_query: str, error_context: Optional[str] = None) -> dict:
        extra = ""
        if error_context:
            extra = f"\nError context from log (use this to sharpen intent): {error_context}\n"
        prompt = f"""Return ONLY valid JSON, no markdown, no explanation.

Fields:
- intent: one of [explain, bug_explanation, usage, optimization, refactor, security, general_question]
- function_name_candidates: list of function or method names mentioned
- raw_query: the original query string
{extra}
User query: "{user_query}"
"""
        response = self.model.generate_content(prompt)
        text = response.text.strip()
        text = re.sub(r"```json|```", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end >= start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass
        return {
            "intent": "bug_explanation",
            "function_name_candidates": [],
            "raw_query": user_query
        }

    def analyze_function(
        self,
        function_code: str,
        function_name: str,
        file_path: str,
        graph_summary: dict,
        retrieved_context: list,
        raw_query: str,
        intent: str,
        depth: int,
        slice_context: Optional[dict] = None,
    ) -> dict:
        """
        Core analysis pass. Returns structured JSON:
        {
          "status": "bug_found" | "clean" | "needs_deeper",
          "bug": { description, line, severity, fix } | null,
          "check_next": ["fn_name1", "fn_name2"],
          "reasoning": "..."
        }
        """

        # ── Build optional error context block ────────────────────────────────
        error_block = ""
        if slice_context:
            err_type = slice_context.get("error_type", "")
            err_msg  = slice_context.get("error_message", "")
            fail_line = slice_context.get("failing_line", "")
            slice_stmts = slice_context.get("slice_statements", [])
            findings_txt = slice_context.get("static_findings", "")

            slice_lines_txt = "\n".join(
                f"  L{ln}: {txt}" for ln, txt in slice_stmts
            ) if slice_stmts else "  (no slice statements resolved)"

            error_block = f"""
ERROR CONTEXT FROM LOG:
{err_type}: {err_msg}

BACKWARD SLICE (statements relevant to the failure at line {fail_line}):
{slice_lines_txt}

STATIC FINDINGS ON SLICE VARIABLES:
{findings_txt or '(none)'}
"""

        prompt = f"""You are a senior software engineer performing deep code analysis.

USER QUERY: {raw_query}
INTENT: {intent}
CURRENT DEPTH: {depth}
{error_block}
FUNCTION BEING ANALYZED: {function_name} in {file_path}
{function_code}

DEPENDENCY GRAPH SO FAR:
{json.dumps(graph_summary, indent=2)}

SIMILAR CODE PATTERNS FROM KNOWLEDGE BASE:
{json.dumps(retrieved_context[:3], indent=2)}

TASK:
Analyze the function above carefully. You MUST return ONLY valid JSON in this exact format:

{{
  "status": "bug_found" | "clean" | "needs_deeper",
  "bug": {{
    "description": "clear description of the bug",
    "line": <line number or null>,
    "severity": "critical" | "moderate" | "minor",
    "fix": "exact fix with corrected code"
  }} or null,
  "check_next": ["function_name_1", "function_name_2"],
  "reasoning": "why you made this decision"
}}

RULES:
- If you find a clear bug in THIS function → status: "bug_found", fill bug field
- If this function looks clean but calls other functions you haven't seen → status: "needs_deeper", list them in check_next
- If this function is clean AND all its calls are already in the graph as clean → status: "clean"
- check_next should only contain function names actually called inside this function
- Do NOT return markdown, only raw JSON
"""


        response = self.model.generate_content(prompt)
        text = response.text.strip()

        # Strip markdown fences if model wraps in them
        text = re.sub(r"```json|```", "", text).strip()
        text = text[text.find("{"):text.rfind("}")+1]

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback safe response
            return {
                "status": "clean",
                "bug": None,
                "check_next": [],
                "reasoning": "Could not parse LLM response, defaulting to clean."
            }

    def generate_final_report(
        self,
        graph_summary: dict,
        bug_info: dict,
        raw_query: str,
        intent: str
    ) -> str:
        prompt = f"""You are a senior software engineer. Write a clear, structured bug report.

USER QUERY: {raw_query}
INTENT: {intent}

BUG FOUND:
{json.dumps(bug_info, indent=2)}

FULL DEPENDENCY GRAPH:
{json.dumps(graph_summary, indent=2)}

Write:
1. Summary of what the bug is
2. Where exactly it was found (function + file)
3. Why it's a problem
4. The exact fix with corrected code
5. Any related functions the developer should double-check

STRICT FORMATTING RULES — follow these exactly:
- Use triple backticks (``` ```) ONLY for actual multi-line code blocks (2 or more lines of real code).
- Use single backticks for inline references: function names, variable names, file names, short expressions like `len(numbers)`.
- NEVER wrap a single word, function name, filename, or one-line expression in triple backticks. These must always be inline backticks.
- Write all explanations, summaries, bullet points, and descriptions as plain prose — not inside any code block.
- Do NOT put error names, return values, parameter names, or any single token inside triple backticks.
- Section 4 (the fix) should show a proper multi-line corrected code block. Everything else explaining the fix should be plain text with inline backticks for names.

CORRECT example:
The bug is in `compute_average()` inside `math_utils.py`. It divides by `len(numbers) - 1` instead of `len(numbers)`.

WRONG example:
The bug is in ```python\ncompute_average\n``` inside ```python\nmath_utils.py\n```.

Be direct, precise, and developer-friendly.
"""
        response = self.model.generate_content(prompt)
        return response.text

    def generate_clean_report(
        self,
        graph_summary: dict,
        raw_query: str,
        intent: str
    ) -> str:
        prompt = f"""You are a senior software engineer.

USER QUERY: {raw_query}
INTENT: {intent}

ANALYSIS COMPLETE - No bugs found.

TRAVERSAL SUMMARY:
{json.dumps(graph_summary, indent=2)}

Write a clear summary of:
1. What was analyzed (functions + files)
2. Confirmation that no bugs were found
3. Any observations, code quality notes, or suggestions based on the intent
4. Which dependencies were checked

Be concise and helpful.
"""
        response = self.model.generate_content(prompt)
        return response.text
