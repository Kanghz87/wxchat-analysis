"""当前联系人全部可读取记录的文字统计；汇总仅由调用方缓存在内存。"""
from collections import Counter
from functools import lru_cache
import logging
import re
from typing import Any

import pandas as pd

from analysis.basic import message_labels
from core.logging_utils import log_failure


# 普通、兼容及扩展汉字；标点、假名和表情不属于汉字。
HAN = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    "\U00020000-\U0002a6df\U0002a700-\U0002b73f"
    "\U0002b740-\U0002b81f\U0002b820-\U0002ceaf"
    "\U0002ceb0-\U0002ebef\U0002ebf0-\U0002ee5f"
    "\U0002f800-\U0002fa1f\U00030000-\U0003134f"
    "\U00031350-\U000323af\U000323b0-\U0003347f]"
)
URL = re.compile(r"(?:https?://|ftp://|www\.)[^\s<>，。！？；、]+", re.IGNORECASE)
# 小型固定虚词表；保留哈哈、晚安等聊天表达，不合并同义词。
STOPWORDS = frozenset("""
的 了 是 我 你 他 她 它 我们 你们 他们 她们 它们
和 与 或 而 及 在 就 都 也 还 又 但 啊 呀 呢 吗 吧 哦 嗯 呃 嘛 呗
这 那 这个 那个 一个 一些 已经 就是 还是 只是 然后 因为 所以 如果 但是
""".split())


@lru_cache(maxsize=1)
def _tokenizer() -> Any:
    # 延迟加载，未安装分词依赖时字数及其他图表仍可用。
    import jieba

    jieba.setLogLevel(logging.WARNING)
    tokenizer = jieba.Tokenizer()
    tokenizer.initialize()
    return tokenizer


def text_statistics(frame: pd.DataFrame) -> dict[str, Any]:
    """只接收完整联系人 DataFrame，不接受日期或图表参数。"""
    result = {
        scope: {"chinese_chars": 0, "all_chars": 0}
        for scope in ("total", "me", "other")
    }
    result.update(words=[], warning="")
    selected = frame.loc[
        message_labels(frame).eq("文字") & frame["sender"].isin(("我", "对方")),
        ["sender", "content"],
    ]
    for sender, content in selected.itertuples(index=False, name=None):
        if not isinstance(content, str) or not content:
            continue
        counts = result["me" if sender == "我" else "other"]
        counts["chinese_chars"] += len(HAN.findall(content))
        counts["all_chars"] += len(content)
    for field in ("chinese_chars", "all_chars"):
        result["total"][field] = result["me"][field] + result["other"][field]
    if not result["total"]["all_chars"]:
        return result

    counters: dict[str, Counter[str]] = {"me": Counter(), "other": Counter()}
    try:
        tokenizer = _tokenizer()
        for sender, content in selected.itertuples(index=False, name=None):
            if not isinstance(content, str) or not content:
                continue
            counter = counters["me" if sender == "我" else "other"]
            for token in tokenizer.cut(URL.sub(" ", content), cut_all=False):
                word = token.strip().casefold()
                if len(word) >= 2 and word not in STOPWORDS and any(char.isalpha() for char in word):
                    counter[word] += 1
    except Exception as error:
        log_failure("统计高频词", error)
        result["warning"] = "高频词暂不可用，字数统计不受影响。请安装 requirements.txt 中的依赖；仍失败时查看本机日志。"
        return result

    total = counters["me"] + counters["other"]
    # 只留在服务内存，忽略/恢复词语时复用 Counter，不重新分词。
    result["_word_counters"] = {"total": total, **counters}
    result["words"] = _word_rows(result["_word_counters"], ())
    return result


def _word_rows(counters: dict[str, Counter[str]], ignored_words: list[str] | tuple[str, ...]) -> list[dict]:
    ignored = set(ignored_words)
    total = counters["total"]
    visible = Counter({word: count for word, count in total.items() if word not in ignored}) if ignored else total
    return [
        {"词语": word, "总次数": count, "我发送": counters["me"][word], "对方发送": counters["other"][word]}
        for word, count in visible.most_common(10)
    ]


def visible_text_statistics(stats: dict, ignored_words: list[str]) -> dict:
    """网页仅获得字数与前十名，不传出完整词频 Counter。"""
    result = {key: value for key, value in stats.items() if not key.startswith("_")}
    if "_word_counters" in stats:
        result["words"] = _word_rows(stats["_word_counters"], ignored_words)
    return result
