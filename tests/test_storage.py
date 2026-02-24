"""
Tests for SQLite storage.
Uses a temp database for each test.
"""

import pytest
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.models import Message


@pytest.fixture
def store(tmp_path):
    """Create a fresh SQLite store for each test."""
    db_path = str(tmp_path / "test.db")
    return SQLiteStore(db_path)


def test_store_and_retrieve(store):
    """Store a message and get it back."""
    msg = Message(conversation_id="conv1", role="user", content="hello world", model="qwen3:32b")
    store.store_message(msg)

    messages = store.get_conversation("conv1")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello world"


def test_multiple_messages(store):
    """Store multiple messages in same conversation."""
    store.store_message(Message(conversation_id="conv1", role="user", content="hi"))
    store.store_message(Message(conversation_id="conv1", role="assistant", content="hello!"))
    store.store_message(Message(conversation_id="conv1", role="user", content="how are you?"))

    messages = store.get_conversation("conv1")
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_separate_conversations(store):
    """Messages in different conversations stay separate."""
    store.store_message(Message(conversation_id="conv1", role="user", content="msg1"))
    store.store_message(Message(conversation_id="conv2", role="user", content="msg2"))

    assert len(store.get_conversation("conv1")) == 1
    assert len(store.get_conversation("conv2")) == 1


def test_stats(store):
    """Stats reflect stored data."""
    store.store_message(Message(conversation_id="c1", role="user", content="a"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="b"))
    store.store_message(Message(conversation_id="c2", role="user", content="c"))

    stats = store.get_stats()
    assert stats["conversations"] == 2
    assert stats["messages"] == 3
    assert stats["user_messages"] == 2
    assert stats["assistant_messages"] == 1


def test_stats_tokens(store):
    """Token stats are tracked correctly."""
    store.store_message(Message(conversation_id="c1", role="user", content="a", token_count=10))
    store.store_message(Message(conversation_id="c1", role="assistant", content="b", token_count=20))
    store.store_message(Message(conversation_id="c1", role="user", content="c", token_count=15))

    stats = store.get_stats()
    tokens = stats["tokens"]
    assert tokens["total"] == 45
    assert tokens["user"] == 25
    assert tokens["assistant"] == 20


def test_stats_models(store):
    """Per-model breakdown is reported."""
    store.store_message(Message(conversation_id="c1", role="user", content="a", model="qwen3:32b", token_count=10))
    store.store_message(Message(conversation_id="c1", role="assistant", content="b", model="qwen3:32b", token_count=20))
    store.store_message(Message(conversation_id="c2", role="user", content="c", model="mistral-nemo:12b", token_count=5))

    stats = store.get_stats()
    models = stats["models"]
    assert "qwen3:32b" in models
    assert models["qwen3:32b"]["messages"] == 2
    assert models["qwen3:32b"]["tokens"] == 30
    assert "mistral-nemo:12b" in models


def test_export_json(store):
    """Export produces OpenAI-compatible format."""
    store.store_message(Message(conversation_id="c1", role="user", content="hello", model="test"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="hi", model="test"))

    export = store.export_all_json()
    assert len(export) == 1
    assert export[0]["conversation_id"] == "c1"
    assert len(export[0]["messages"]) == 2
    assert export[0]["messages"][0]["role"] == "user"


# ---------------------------------------------------------------------------
# Export formats
# ---------------------------------------------------------------------------

def test_export_jsonl_basic(store):
    """JSONL export produces one dict per conversation with messages list."""
    store.store_message(Message(conversation_id="c1", role="user", content="hi", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="hello", model="m"))

    data = store.export_jsonl()
    assert len(data) == 1
    assert data[0]["messages"][0] == {"role": "user", "content": "hi"}
    assert data[0]["messages"][1] == {"role": "assistant", "content": "hello"}


def test_export_jsonl_skips_system_messages(store):
    """JSONL export excludes system role messages."""
    store.store_message(Message(conversation_id="c1", role="system", content="sys", model="m"))
    store.store_message(Message(conversation_id="c1", role="user", content="hi", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="hello", model="m"))

    data = store.export_jsonl()
    roles = [m["role"] for m in data[0]["messages"]]
    assert "system" not in roles
    assert len(roles) == 2


def test_export_jsonl_skips_incomplete_conversations(store):
    """JSONL export excludes conversations with only user or only assistant messages."""
    store.store_message(Message(conversation_id="user-only", role="user", content="hi", model="m"))
    store.store_message(Message(conversation_id="asst-only", role="assistant", content="hi", model="m"))
    store.store_message(Message(conversation_id="complete", role="user", content="hi", model="m"))
    store.store_message(Message(conversation_id="complete", role="assistant", content="hello", model="m"))

    data = store.export_jsonl()
    assert len(data) == 1
    assert data[0]["messages"][0]["content"] == "hi"


def test_export_jsonl_model_filter(store):
    """JSONL export filters to specified model."""
    store.store_message(Message(conversation_id="c1", role="user", content="a", model="model-a"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="b", model="model-a"))
    store.store_message(Message(conversation_id="c2", role="user", content="c", model="model-b"))
    store.store_message(Message(conversation_id="c2", role="assistant", content="d", model="model-b"))

    data = store.export_jsonl(model_filter="model-a")
    assert len(data) == 1
    assert data[0]["messages"][0]["content"] == "a"


def test_export_alpaca_basic(store):
    """Alpaca export produces instruction/output pairs per turn."""
    store.store_message(Message(conversation_id="c1", role="user", content="what is 2+2", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="4", model="m"))

    data = store.export_alpaca()
    assert len(data) == 1
    assert data[0]["instruction"] == "what is 2+2"
    assert data[0]["input"] == ""
    assert data[0]["output"] == "4"


def test_export_alpaca_multi_turn(store):
    """Alpaca export produces one record per user/assistant pair."""
    store.store_message(Message(conversation_id="c1", role="user", content="q1", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="a1", model="m"))
    store.store_message(Message(conversation_id="c1", role="user", content="q2", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="a2", model="m"))

    data = store.export_alpaca()
    assert len(data) == 2
    assert data[0]["instruction"] == "q1"
    assert data[1]["instruction"] == "q2"


def test_export_sharegpt_basic(store):
    """ShareGPT export maps roles to human/gpt."""
    store.store_message(Message(conversation_id="c1", role="user", content="hello", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="hi", model="m"))

    data = store.export_sharegpt()
    assert len(data) == 1
    assert data[0]["id"] == "c1"
    convs = data[0]["conversations"]
    assert convs[0] == {"from": "human", "value": "hello"}
    assert convs[1] == {"from": "gpt", "value": "hi"}


def test_export_sharegpt_includes_system(store):
    """ShareGPT export includes system messages mapped to 'system'."""
    store.store_message(Message(conversation_id="c1", role="system", content="be helpful", model="m"))
    store.store_message(Message(conversation_id="c1", role="user", content="hi", model="m"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="hello", model="m"))

    data = store.export_sharegpt()
    assert data[0]["conversations"][0]["from"] == "system"
    assert len(data[0]["conversations"]) == 3


def test_export_multiple_conversations(store):
    """All three formats handle multiple conversations correctly."""
    for cid in ("c1", "c2", "c3"):
        store.store_message(Message(conversation_id=cid, role="user", content=f"q-{cid}", model="m"))
        store.store_message(Message(conversation_id=cid, role="assistant", content=f"a-{cid}", model="m"))

    assert len(store.export_jsonl()) == 3
    assert len(store.export_alpaca()) == 3
    assert len(store.export_sharegpt()) == 3

