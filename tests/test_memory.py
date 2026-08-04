"""会话记忆 key 结构测试 —— 确保 Redis key 命名约定不被破坏。"""

from core.memory import MemoryManager


def test_message_key_structure():
    assert MemoryManager._msg_key("abc") == "lg:session:abc:messages"


def test_meta_key_structure():
    assert MemoryManager._meta_key("abc") == "lg:session:abc:meta"


def test_summary_flag_key_structure():
    assert MemoryManager._summary_flag_key("abc") == "lg:session:abc:needs_summary"


def test_fallback_mode_when_not_connected():
    # 未 connect 时 _connected 为 False，读写走内存 fallback
    mm = MemoryManager()
    assert mm._connected is False
