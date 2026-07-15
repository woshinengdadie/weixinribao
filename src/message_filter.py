"""
消息过滤与参与度分析模块

核心逻辑：
1. 找出"我"发了消息的会话 → 提取完整对话上下文
2. 找出 @了我 但 我没有回复的 → 标记为待办
3. 我没有参与的消息 → 过滤掉
4. 重要联系人发送的消息 → 即使未@我也保留（确保关键人物发言不漏）
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from wechat_reader import _normalize_chat_name

logger = logging.getLogger(__name__)


class MessageFilter:
    """消息参与度分析器"""

    def __init__(self, config: dict):
        self._config = config
        self._my_name = config.get("wechat", {}).get("my_name", "")
        self._exclude_chats_list = config.get("monitor", {}).get("exclude_chats", [])
        self._important_contacts = config.get("monitor", {}).get("important_contacts", [])
        # 预计算屏蔽集合（规范化后精确匹配）
        self._exclude_normalized = {
            _normalize_chat_name(ex) for ex in self._exclude_chats_list if ex and ex.strip()
        }
        # 预计算重要联系人集合（规范化）
        self._important_normalized = {
            _normalize_chat_name(c) for c in self._important_contacts if c and c.strip()
        }

    def _empty_result(self):
        """返回空的分析结果"""
        return {
            "my_messages": [],
            "my_chats": [],
            "my_count": 0,
            "unreplied": [],
            "unreplied_count": 0,
            "all_messages": [],
            "total_messages": 0,
            "mentioned_me": [],
        }

    def _is_me(self, msg: Dict) -> bool:
        """判断消息是否是我发的"""
        sender = msg.get("sender", "")
        if not sender or not self._my_name:
            return False
        return self._my_name in sender or sender in self._my_name

    def _mentions_me(self, msg: Dict) -> bool:
        """判断消息是否提到了我"""
        content = msg.get("content", "")
        if not content or not self._my_name:
            return False
        # 检查 @我 或直接提到名字
        if f"@{self._my_name}" in content:
            return True
        keys = ["@我", "@所有人"]
        return any(k in content for k in keys)

    def _get_sort_time(self, msg: Dict) -> str:
        """获取用于排序的时间"""
        return msg.get("time_sort", msg.get("time", ""))

    def _time_diff_seconds(self, t1: str, t2: str) -> float:
        """计算两个时间字符串的秒数差"""
        try:
            dt1 = datetime.strptime(t1[:19], "%Y-%m-%d %H:%M:%S")
            dt2 = datetime.strptime(t2[:19], "%Y-%m-%d %H:%M:%S")
            return abs((dt1 - dt2).total_seconds())
        except:
            return 999999

    def _is_from_important(self, msg: Dict) -> bool:
        """判断消息是否来自重要联系人（按发送者名字匹配）

        私聊场景下 sender 可能与 chat 相同；群聊场景下 sender 是发言者昵称。
        匹配规则：sender 规范化后在 important_contacts 集合中（双向包含，容错）。
        """
        if not self._important_normalized:
            return False
        sender = msg.get("sender", "")
        if not sender:
            return False
        sender_norm = _normalize_chat_name(sender)
        if sender_norm in self._important_normalized:
            return True
        # 双向包含，容错发送者带前后缀的情况（如 "老板-张总" 包含 "张总"）
        for contact in self._important_normalized:
            if contact and (contact in sender_norm or sender_norm in contact):
                return True
        return False

    def _exclude_chats(self, messages: List[Dict]) -> List[Dict]:
        """排除不关注的会话（规范化后精确匹配，与 wechat_reader 一致）"""
        if not self._exclude_normalized:
            return messages
        result = []
        for msg in messages:
            chat = msg.get("chat", "")
            if _normalize_chat_name(chat) not in self._exclude_normalized:
                result.append(msg)
        return result

    def _group_by_chat(self, messages: List[Dict]) -> Dict[str, List[Dict]]:
        """按会话分组"""
        groups = defaultdict(list)
        for msg in messages:
            chat = msg.get("chat", "未知会话")
            groups[chat].append(msg)
        return dict(groups)

    def _has_reply_after(self, chat_msgs: List[Dict], mention_msg: Dict) -> bool:
        """检查 @我的消息之后，我是否有回复。

        通过消息在列表中位置判断：@我的消息索引之后的任一条为"我发的"即视为已回复。
        """
        mention_idx = -1
        mention_content = mention_msg.get("content", "")
        mention_time = mention_msg.get("time_sort", mention_msg.get("time", ""))
        mention_sender = mention_msg.get("sender", "")

        for i, msg in enumerate(chat_msgs):
            # 用内容+时间+发送者三元组匹配（比 is 更可靠）
            if (msg.get("content", "") == mention_content
                    and msg.get("time_sort", msg.get("time", "")) == mention_time
                    and msg.get("sender", "") == mention_sender):
                mention_idx = i
                break

        if mention_idx < 0:
            return False

        # 查找之后是否有我的消息
        for j in range(mention_idx + 1, len(chat_msgs)):
            if self._is_me(chat_msgs[j]):
                return True

        return False

    def _summarize_conversation(self, chat_msgs: List[Dict], my_msgs: List[Dict]) -> str:
        """生成对话摘要"""
        if not chat_msgs:
            return ""

        # 收集对话片段
        snippets = []
        for msg in chat_msgs:
            sender = msg.get("sender", "未知")
            content = msg.get("content", "")
            time_str = msg.get("time", "")
            if content:
                snippets.append(f"[{time_str}] {sender}: {content[:200]}")

        return "\n".join(snippets)

    def analyze(self, messages: List[Dict]) -> Dict:
        """主分析入口：分析消息参与度

        Args:
            messages: 所有消息列表

        Returns:
            {
                "my_messages": [...],           # 我自己发的消息
                "my_chats": [...],              # 我参与的会话
                "my_count": int,                # 我的消息数
                "unreplied": [...],             # @我但未回复的消息
                "unreplied_count": int,         # 未回复数量
                "all_messages": [...],          # 所有消息
                "total_messages": int,          # 总消息数
                "mentioned_me": [...],          # 提到我的消息
            }
        """
        if not messages:
            logger.info("analyze: 输入消息数为 0，返回空结果")
            return self._empty_result()

        # 1. 排除不需要的会话
        filtered = self._exclude_chats(messages)
        logger.info(
            f"analyze: 排除屏蔽会话 {len(messages)} → {len(filtered)} 条消息"
            if len(filtered) < len(messages)
            else f"analyze: {len(messages)} 条消息（无屏蔽过滤）"
        )

        if not filtered:
            logger.warning("analyze: 排除后无消息")
            return self._empty_result()

        # 2. 按会话分组
        chat_groups = self._group_by_chat(filtered)
        logger.info(f"analyze: {len(filtered)} 条消息分布在 {len(chat_groups)} 个会话")

        # 3. 分析每个会话
        my_messages = []
        my_chats = []
        unreplied = []
        mentioned_me = []
        private_unreplied = 0

        for chat, chat_msgs in chat_groups.items():
            # 判断是群聊还是私聊
            is_group = any(m.get("is_group", False) for m in chat_msgs)

            # 找出我的消息
            chat_my_msgs = [m for m in chat_msgs if self._is_me(m)]

            # === 群聊逻辑 ===
            if is_group:
                # 群聊：只关注 @我、我参与了、或重要联系人发送的消息
                relevant_msgs = [m for m in chat_msgs
                                 if self._is_me(m) or self._mentions_me(m) or self._is_from_important(m)]
                if not relevant_msgs:
                    continue  # 与我无关的群聊，跳过

                if chat_my_msgs:
                    my_messages.extend(chat_my_msgs)
                    my_chats.append({
                        "chat": chat,
                        "my_messages": chat_my_msgs,
                        "all_messages": relevant_msgs,  # 只保留相关消息
                        "conversation": self._summarize_conversation(relevant_msgs, chat_my_msgs),
                    })

                # @我但未回复
                for msg in chat_msgs:
                    if self._mentions_me(msg) and not self._is_me(msg):
                        mentioned_me.append(msg)
                        if not self._has_reply_after(chat_msgs, msg):
                            unreplied.append({
                                "chat": chat,
                                "message": msg,
                                "context": self._summarize_conversation(
                                    [m for m in chat_msgs if m is msg or
                                     abs(self._time_diff_seconds(
                                         m.get("time_sort", m.get("time", "")),
                                         msg.get("time_sort", msg.get("time", ""))
                                     )) < 300],
                                    []
                                ),
                            })

            # === 私聊逻辑 ===
            else:
                # 私聊：不管我有没有回复，全部保留
                if chat_my_msgs:
                    my_messages.extend(chat_my_msgs)
                # 全部加入总结
                my_chats.append({
                    "chat": chat,
                    "my_messages": chat_my_msgs,
                    "all_messages": chat_msgs,  # 私聊保留全部消息
                    "conversation": self._summarize_conversation(chat_msgs, chat_my_msgs),
                })
                # 如果我没回复，标记为待回复
                if not chat_my_msgs and chat_msgs:
                    other_msgs = [m for m in chat_msgs if not self._is_me(m)]
                    if other_msgs:
                        last_msg = other_msgs[-1]
                        unreplied.append({
                            "chat": chat,
                            "message": last_msg,
                            "context": self._summarize_conversation(chat_msgs, []),
                            "source": "private_chat",
                        })
                        private_unreplied += 1

        result = {
            "my_messages": my_messages,
            "my_chats": my_chats,
            "my_count": len(my_messages),
            "unreplied": unreplied,
            "unreplied_count": len(unreplied),
            "all_messages": filtered,
            "total_messages": len(filtered),
            "mentioned_me": mentioned_me,
        }
        logger.info(
            f"analyze 完成: my_count={result['my_count']}, "
            f"my_chats={len(my_chats)}, "
            f"mentioned_me={len(mentioned_me)}, "
            f"unreplied={result['unreplied_count']} "
            f"(其中私聊未回复: {private_unreplied})"
        )
        if self._my_name and result['my_count'] == 0:
            logger.warning(
                f"未匹配到任何 'my_name'='{self._my_name}' 的消息! 请确认微信昵称配置是否正确。"
            )
        return result
