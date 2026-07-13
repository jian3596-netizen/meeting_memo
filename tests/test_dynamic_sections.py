import unittest

from app import export, templates_prompts
from app.llm import OpenAICompatLLM
from app.models import MeetingSummary, Segment


CUSTOMER_SECTIONS = [
    {"id": "customer_info", "title": "客户信息", "prompt": "提取客户背景和联系人"},
    {"id": "needs", "title": "客户需求", "prompt": "提取明确需求和优先级"},
]


class DynamicSectionsTest(unittest.TestCase):
    def test_prompt_uses_only_configured_sections_in_order(self) -> None:
        messages = templates_prompts.build_summary_messages(
            "[00:00:01] 客户：我们需要缩短交付时间。",
            "客户拜访",
            "关注客户情况",
            CUSTOMER_SECTIONS,
        )
        user_prompt = messages[-1]["content"]

        self.assertLess(user_prompt.index("客户信息"), user_prompt.index("客户需求"))
        self.assertIn('"id": "customer_info"', user_prompt)
        self.assertNotIn('"id": "risks"', user_prompt)

    def test_llm_result_is_aligned_to_category_configuration(self) -> None:
        llm = OpenAICompatLLM.__new__(OpenAICompatLLM)
        llm._chat = lambda messages, json_mode=True: """{
            "title": "拜访纪要",
            "sections": [
                {"id": "needs", "title": "模型擅自改名", "content": "需要更快交付 [00:00:01]"},
                {"id": "extra", "title": "多余章节", "content": "不应保留"}
            ]
        }"""

        summary = llm._chat_to_summary([], CUSTOMER_SECTIONS)

        self.assertEqual([s.id for s in summary.sections], ["customer_info", "needs"])
        self.assertEqual([s.title for s in summary.sections], ["客户信息", "客户需求"])
        self.assertEqual(summary.sections[0].content, "未提及")
        self.assertIn("更快交付", summary.sections[1].content)

    def test_export_prefers_dynamic_sections_over_legacy_fields(self) -> None:
        summary = MeetingSummary.model_validate({
            "title": "客户拜访",
            "sections": [
                {"id": "customer_info", "title": "客户信息", "content": "甲公司"},
            ],
            "risks": [{"content": "旧风险", "source_time": "00:01:00"}],
        })
        markdown = export.to_markdown({}, summary, [Segment(text="原文")])

        self.assertIn("## 客户信息", markdown)
        self.assertNotIn("## 风险问题", markdown)


if __name__ == "__main__":
    unittest.main()
