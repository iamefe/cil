"""9-case MCP stdio regression suite for CIL (newline-delimited JSON-RPC).

Run: .venv/bin/python mcp_stdio_suite.py   (from the cil repo root)
"""
import json
import subprocess
import sys
import threading

REPO = "/Users/iamefe/Documents/projects/cil"
CIL = f"{REPO}/.venv/bin/cil"


def client():
    proc = subprocess.Popen(
        [CIL, "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=REPO,
        text=True,
    )
    return proc


def rpc(proc, req_id, method, params):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line)


def tool_call(proc, req_id, name, args):
    res = rpc(proc, req_id, "tools/call", {"name": name, "arguments": args})
    if "error" in res:
        return None, res["error"]
    content = res.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return text, None


def main():
    proc = client()
    init = rpc(proc, 0, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "suite", "version": "1"}})
    assert "result" in init, f"initialize failed: {init}"

    results = []

    def check(label, fn):
        try:
            ok, detail = fn()
            results.append((label, ok, detail))
        except Exception as e:  # noqa: BLE001
            results.append((label, False, f"EXC {e}"))

    # 1. db status
    def t1():
        text, err = tool_call(proc, 1, "cil_db_status", {})
        return (err is None and "sqlite" in text.lower()), (err or text[:120])
    check("db_status", t1)

    # 2. status lists both projects
    def t2():
        text, err = tool_call(proc, 2, "cil_status", {})
        ok = err is None and "cil" in text and "trading-bot-multi" in text
        return ok, (err or text[:200])
    check("status_two_projects", t2)

    # 3. find_symbol upsert @ cil — EXACTLY 2 active rows (regression: status filter)
    def t3():
        text, err = tool_call(proc, 3, "cil_find_symbol", {"name": "upsert", "project": "cil"})
        if err:
            return False, err
        rows = json.loads(text)
        ok = len(rows) == 2 and {r["name"] for r in rows} == {"upsert_project", "upsert_file"}
        return ok, f"rows={len(rows)}"
    check("find_symbol_active_only", t3)

    # 4. find_symbol unknown — empty, no error
    def t4():
        text, err = tool_call(proc, 4, "cil_find_symbol", {"name": "zzz_no_such_symbol_qq", "project": "cil"})
        return (err is None and text.strip() in ("[]", "")), (err or text[:80])
    check("find_symbol_unknown_empty", t4)

    # 5. file_summary — symbols present, no duplicate names
    def t5():
        text, err = tool_call(proc, 5, "cil_file_summary", {"path": "src/cil/sqlite_db.py", "project": "cil"})
        if err:
            return False, err
        data = json.loads(text)
        names = [s["name"] for s in data.get("symbols", [])]
        ok = len(names) > 10 and len(names) == len(set(names))
        return ok, f"symbols={len(names)} dups={len(names) - len(set(names))}"
    check("file_summary_no_dupes", t5)

    # 6. trace_calls store_index — has callers and callees
    def t6():
        text, err = tool_call(proc, 6, "cil_trace_calls", {"func_name": "store_index", "project": "cil"})
        if err:
            return False, err
        data = json.loads(text)
        ok = len(data.get("callers", [])) > 0 and len(data.get("callees", [])) > 0
        return ok, f"callers={len(data.get('callers', []))} callees={len(data.get('callees', []))}"
    check("trace_calls", t6)

    # 7. trace_mutations — runs cleanly
    def t7():
        text, err = tool_call(proc, 7, "cil_trace_mutations", {"target": "file_indices", "project": "cil"})
        return (err is None and text.strip() != ""), (err or f"hits={len(json.loads(text)) if text.strip().startswith('[') else 'n/a'}")
    check("trace_mutations", t7)

    # 8. get_anomalies — list returns
    def t8():
        text, err = tool_call(proc, 8, "cil_get_anomalies", {"project": "cil"})
        return (err is None and text.strip().startswith("[")), (err or f"n={len(json.loads(text))}")
    check("get_anomalies", t8)

    # 9. cross-project: find run_multi @ trading-bot-multi (scope isolation)
    def t9():
        text, err = tool_call(proc, 9, "cil_find_symbol", {"name": "run_multi", "project": "trading-bot-multi"})
        if err:
            return False, err
        rows = json.loads(text)
        ok = len(rows) >= 1 and all("trading-bot-multi" in r["file_path"] for r in rows)
        return ok, f"rows={len(rows)}"
    check("cross_project_scope", t9)

    proc.stdin.close()
    proc.terminate()

    passed = sum(1 for _, ok, _ in results if ok)
    for label, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label:28s} {detail}")
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
