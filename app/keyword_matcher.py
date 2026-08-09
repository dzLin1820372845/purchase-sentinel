"""AC 自动机关键词匹配器"""
import ahocorasick


class KeywordMatcher:
    """Thread-safe keyword matcher using AC automaton.

    匹配结果格式: [{"keyword": "处罚", "category": "综合"}, ...]
    rebuild() 创建新自动机替换旧引用（原子交换模式）。
    """

    def __init__(self):
        self._automaton: ahocorasick.Automaton | None = None

    def build(self, keywords: list[dict[str, str]]) -> None:
        """从关键词列表构建 AC 自动机。"""
        auto = ahocorasick.Automaton()
        for kw_data in keywords:
            auto.add_word(kw_data["keyword"], kw_data)
        auto.make_automaton()
        self._automaton = auto

    def match(self, text: str) -> list[dict[str, str]]:
        """扫描文本，返回去重后的匹配结果。"""
        if not self._automaton or not text:
            return []

        seen: set[str] = set()
        results: list[dict[str, str]] = []
        for _, value in self._automaton.iter(text):
            key = value["keyword"]
            if key not in seen:
                seen.add(key)
                results.append(value)
        return results

    def rebuild(self, keywords: list[dict[str, str]]) -> None:
        """用新关键词重建自动机（原子替换）。"""
        self.build(keywords)
