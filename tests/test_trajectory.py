"""Tests for beigebox.trajectory score_run()."""
import pytest
from beigebox.trajectory import score_run, _is_coding_task, _words, _CODING_KEYWORDS


# ── helpers ────────────────────────────────────────────────────────────────

def _tc(tool="web_search", inp="query"):
    return {"type": "tool_call", "tool": tool, "input": inp}

def _tr(tool="web_search"):
    return {"type": "tool_result", "tool": tool, "result": "result"}

def _ans(content="done"):
    return {"type": "answer", "content": content}

def _ts(turn=2, total=5):
    return {"type": "turn_start", "turn": turn, "total": total}

def _err():
    return {"type": "error", "message": "something broke"}

def _ws_write():
    return {"type": "tool_call", "tool": "workspace_file",
            "input": '{"action": "write", "path": "foo.py", "content": "x"}'}


# ── _is_coding_task ─────────────────────────────────────────────────────────

def test_coding_task_detected():
    assert _is_coding_task("write a function that sorts a list")
    assert _is_coding_task("Build me a REST API endpoint")
    assert _is_coding_task("implement a binary search")

def test_non_coding_task():
    assert not _is_coding_task("what is the capital of France")
    assert not _is_coding_task("summarise this document")


# ── happy-path single-turn run ───────────────────────────────────────────────

def test_single_turn_no_tools():
    events = [_ans("Paris is the capital of France.")]
    result = score_run("what is the capital", events, max_turns=1, final_answer="Paris is the capital of France.")
    assert result["turns_used"] == 1
    assert result["tool_calls"] == 0
    assert result["flags"] == []
    assert 0 <= result["score"] <= 10

def test_single_turn_with_tools():
    events = [_tc(), _tr(), _ans("answer")]
    result = score_run("search for something", events, max_turns=3, final_answer="answer")
    assert result["tool_calls"] == 1
    assert result["turns_used"] == 1


# ── multi-turn run ───────────────────────────────────────────────────────────

def test_multi_turn_done_flag():
    events = [
        _tc(), _tr(), _ans("step 1 done"),
        _ts(2, 3), _tc(), _tr(), _ans("step 2 done ##DONE##"),
    ]
    result = score_run("build an app", events, max_turns=3, final_answer="step 2 done ##DONE##")
    assert result["turns_used"] == 2
    assert "loop_detected" not in result["flags"]
    # quality not penalised for ##DONE## present
    assert result["quality"] == 10.0

def test_missing_done_penalises_quality():
    events = [_tc(), _ans("some answer without done marker")]
    result = score_run("build something", events, max_turns=5, final_answer="some answer without done marker")
    assert result["quality"] < 10.0  # −3 for missing ##DONE## in multi-turn

def test_error_events_penalise_quality():
    events = [_tc(), _err(), _err(), _ans("partial")]
    result = score_run("do something", events, max_turns=3, final_answer="partial ##DONE##")
    assert result["quality"] <= 6.0  # −4 for 2 errors


# ── loop detection ───────────────────────────────────────────────────────────

def test_loop_detected():
    events = [
        _tc("web_search", "same query"),
        _tr(),
        _tc("web_search", "same query"),
        _tr(),
        _tc("web_search", "same query"),
        _tr(),
        _ans("answer"),
    ]
    result = score_run("search thing", events, max_turns=5, final_answer="answer ##DONE##")
    assert "loop_detected" in result["flags"]
    assert result["flow"] < 10.0

def test_no_loop_with_different_inputs():
    events = [
        _tc("web_search", "query 1"), _tr(),
        _tc("web_search", "query 2"), _tr(),
        _tc("web_search", "query 3"), _tr(),
        _ans("answer"),
    ]
    result = score_run("research topic", events, max_turns=5, final_answer="answer ##DONE##")
    assert "loop_detected" not in result["flags"]


# ── hit_turn_cap flag ─────────────────────────────────────────────────────────

def test_hit_turn_cap_flag():
    events = [_tc(), _ans("a"),
              _ts(2, 5), _tc(), _ans("b"),
              _ts(3, 5), _tc(), _ans("c"),
              _ts(4, 5), _tc(), _ans("d ##DONE##")]
    result = score_run("task", events, max_turns=5, final_answer="d ##DONE##")
    assert "hit_turn_cap" in result["flags"]  # 4/5 = 80%
    assert result["efficiency"] < 10.0

def test_no_turn_cap_well_under():
    events = [_tc(), _ans("done ##DONE##")]
    result = score_run("task", events, max_turns=10, final_answer="done ##DONE##")
    assert "hit_turn_cap" not in result["flags"]


# ── no_file_writes flag ───────────────────────────────────────────────────────

def test_no_file_writes_coding_task():
    events = [_tc("web_search", "python syntax"), _tr(), _ans("here is code ##DONE##")]
    result = score_run("write a python function", events, max_turns=3, final_answer="here is code ##DONE##")
    assert "no_file_writes" in result["flags"]

def test_file_writes_present_coding_task():
    events = [_ws_write(), _tr(), _ans("done ##DONE##")]
    result = score_run("implement a module", events, max_turns=3, final_answer="done ##DONE##")
    assert "no_file_writes" not in result["flags"]

def test_no_file_writes_non_coding_task():
    # Non-coding task: flag should NOT fire even without writes
    events = [_tc(), _tr(), _ans("Paris ##DONE##")]
    result = score_run("what is the capital of France", events, max_turns=3, final_answer="Paris ##DONE##")
    assert "no_file_writes" not in result["flags"]


# ── score bounds ──────────────────────────────────────────────────────────────

def test_score_always_in_range():
    for events in [
        [],
        [_err(), _err(), _err()],
        [_tc("web_search", "x")] * 5 + [_ans("")],
    ]:
        result = score_run("task", events, max_turns=5, final_answer="")
        assert 0.0 <= result["score"] <= 10.0
        assert 0.0 <= result["flow"] <= 10.0
        assert 0.0 <= result["efficiency"] <= 10.0
        assert 0.0 <= result["quality"] <= 10.0
        assert 0.0 <= result["intent"] <= 10.0

def test_perfect_run_scores_high():
    events = [_ws_write(), _tr(), _ans("Done! ##DONE##")]
    result = score_run(
        "implement a function",
        events,
        max_turns=10,
        final_answer="Done! ##DONE##",
    )
    assert result["score"] >= 7.0
    assert result["flags"] == []
