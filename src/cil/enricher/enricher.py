import json
import os
from typing import Optional

from cil.models import SymbolInfo


class SemanticEnricher:
    """Use an LLM to enrich symbols with purpose, complexity, and audit notes."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        self.model = model or os.environ.get("CIL_LLM_MODEL", "gpt-4o-mini")

    def enrich_symbol(self, symbol: SymbolInfo, source_lines: str) -> SymbolInfo:
        """Enrich a single symbol with LLM analysis."""
        if not self.api_key:
            return symbol

        prompt = self._build_prompt(symbol, source_lines)
        try:
            result = self._call_llm(prompt)
            return self._parse_result(symbol, result)
        except Exception as e:
            print(f"Warning: LLM enrichment failed for {symbol.name}: {e}")
            return symbol

    def enrich_batch(self, symbols: list[SymbolInfo], source_lines_map: dict[str, str]) -> list[SymbolInfo]:
        """Enrich multiple symbols in a single LLM call for efficiency."""
        if not self.api_key:
            return symbols

        if not symbols:
            return symbols

        # Build batch prompt
        prompt = self._build_batch_prompt(symbols, source_lines_map)
        try:
            result = self._call_llm(prompt)
            return self._parse_batch_result(symbols, result)
        except Exception as e:
            print(f"Warning: LLM batch enrichment failed: {e}")
            return symbols

    def _build_prompt(self, symbol: SymbolInfo, source_lines: str) -> str:
        return f"""Analyze this Python symbol and return JSON:

Symbol: {symbol.name}
Kind: {symbol.kind}
File: {symbol.file_path}
Lines: {symbol.line_start}-{symbol.line_end}
Signature: {symbol.signature}
Docstring: {symbol.docstring}
Decorators: {', '.join(symbol.decorators) if symbol.decorators else 'none'}

Source (first 50 lines):
{source_lines[:3000]}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "purpose": "what this symbol does in one sentence",
  "complexity": "low|medium|high",
  "risk_flags": ["flag1", "flag2"],
  "audit_notes": "security or quality concerns"
}}

Risk flags to consider: side_effect, global_state, io_operation, network_call,
database_access, file_write, eval_exec, exception_suppression, mutable_default,
thread_unsafe, resource_leak, hardcoded_secret, weak_crypto, sql_injection,
csrf, xss, path_traversal, privilege_escalation"""

    def _build_batch_prompt(self, symbols: list[SymbolInfo], source_lines_map: dict[str, str]) -> str:
        symbol_descriptions = []
        for i, sym in enumerate(symbols, 1):
            source = source_lines_map.get(sym.file_path, "")[:2000]
            symbol_descriptions.append(f"""Symbol {i}: {sym.name}
Kind: {sym.kind}
Signature: {sym.signature}
Docstring: {sym.docstring[:200]}
Decorators: {', '.join(sym.decorators) if sym.decorators else 'none'}
Source:
{source}""")

        return f"""Analyze these Python symbols and return JSON. For each symbol, provide purpose, complexity, risk_flags, and audit_notes.

Symbols:
{chr(10).join(symbol_descriptions)}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "symbols": [
    {{
      "index": 1,
      "purpose": "what this symbol does",
      "complexity": "low|medium|high",
      "risk_flags": ["flag1"],
      "audit_notes": "concerns"
    }}
  ]
}}

Risk flags: side_effect, global_state, io_operation, network_call, database_access,
file_write, eval_exec, exception_suppression, mutable_default, thread_unsafe,
resource_leak, hardcoded_secret, weak_crypto, sql_injection, csrf, xss,
path_traversal, privilege_escalation"""

    def _call_llm(self, prompt: str) -> str:
        """Make an OpenAI-compatible API call."""
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a code analysis assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.URLError as e:
            raise ConnectionError(f"LLM API call failed: {e}")

    def _parse_result(self, symbol: SymbolInfo, llm_output: str) -> SymbolInfo:
        """Parse LLM JSON response and update symbol."""
        try:
            # Strip markdown code blocks if present
            text = llm_output.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)

            symbol.purpose = data.get("purpose", "")
            symbol.complexity = data.get("complexity", "")
            symbol.risk_flags = list(set(symbol.risk_flags) | set(data.get("risk_flags", [])))
            symbol.audit_notes = data.get("audit_notes", "")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: failed to parse LLM output for {symbol.name}: {e}")

        return symbol

    def _parse_batch_result(self, symbols: list[SymbolInfo], llm_output: str) -> list[SymbolInfo]:
        """Parse batch LLM JSON response and update symbols."""
        try:
            text = llm_output.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)

            results = {item["index"]: item for item in data.get("symbols", [])}

            for i, sym in enumerate(symbols, 1):
                if i in results:
                    r = results[i]
                    sym.purpose = r.get("purpose", "")
                    sym.complexity = r.get("complexity", "")
                    sym.risk_flags = list(set(sym.risk_flags) | set(r.get("risk_flags", [])))
                    sym.audit_notes = r.get("audit_notes", "")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: failed to parse batch LLM output: {e}")

        return symbols
