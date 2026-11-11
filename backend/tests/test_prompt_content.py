from types import SimpleNamespace

import pytest

from app.errors import ApiError
from app.prompt_content import extract_prompt, has_prompt_body
from app.workflow.markdown_handoff import project_output

BOARD = "# 大富翁棋盘 · 生成提示词\n\n## 正向提示词\n\n**画面类型**\n32格蜿蜒闭合棋盘…\n\n## 反向提示词\n\n**路径错误**\n不要断路"


def context():
    return SimpleNamespace(
        settings=SimpleNamespace(visual=lambda: SimpleNamespace(image_size="1024x1024")),
        project=SimpleNamespace(reference_asset_id=None),
    )


def test_board_projection_preserves_bold_body():
    data = project_output(context(), "generation_engineer", BOARD, {})["data"]
    assert data["positive_prompt"] == "**画面类型**\n32格蜿蜒闭合棋盘…"
    assert data["negative_prompt"] == "**路径错误**\n不要断路"

@pytest.mark.parametrize("text", [
    "", " \n\t", "#", "# 正向提示词\n\n## 反向提示词", "```", "```text\n```",
    "~~~markdown\n# 标题\n~~~", "标题\n====", "正向提示词：\n反向提示词：",
    "# 正向提示词\n```text\n## 只有标题\n```",
    "**正向提示词**\n**反向提示词**", "---\n***\n- [ ]", "# Heading\n```text\n```",
])
def test_empty_markup_is_not_prompt_body(text):
    assert not has_prompt_body(text)
    assert extract_prompt(text).positive_prompt == ""
    with pytest.raises(ApiError) as error:
        project_output(context(), "generation_engineer", text, {})
    assert error.value.status_code == 409
    assert error.value.code == "PROMPT_EMPTY"
    assert error.value.message == "提示词没有可用正文，请重新整理后继续。"


@pytest.mark.parametrize("text", [
    "一只猫", "32", "**一只猫**", "- 一只猫", "`cat`", "```text\n一只猫\n```",
    "# 画面\n一只猫", "## 正向提示词\n### 主体\n- 一只猫",
])
def test_meaningful_prompt_body(text):
    assert has_prompt_body(text)
    assert has_prompt_body(extract_prompt(text).positive_prompt)


@pytest.mark.parametrize(("doc", "positive", "negative"), [
    ("**正向提示词**\n### 主体\n猫\n**反向提示词**\n模糊", "### 主体\n猫", "模糊"),
    (BOARD, "**画面类型**\n32格蜿蜒闭合棋盘…", "**路径错误**\n不要断路"),
    ("# 生成提示词\n## 正向提示词\n猫\n## 反向提示词\n模糊", "猫", "模糊"),
    ("# Prompt\n## Positive Prompt\n### Subject\n**cat**\n- red\n## Negative Prompt\nblur", "### Subject\n**cat**\n- red", "blur"),
    ("**Positive Prompt:** a cat\n**Negative Prompt**: blur", "a cat", "blur"),
    ("- 正向提示词：猫\n  - 红色\n- 负向提示词：模糊", "猫\n  - 红色", "模糊"),
    ("正向提示词：\n猫\n反向提示词：\n模糊", "猫", "模糊"),
    ("Positive Prompt\n===\na cat\nNegative Prompt\n===\nblur", "a cat", "blur"),
    ("# 正向提示词\n猫\n# 反向提示词\n", "猫", ""),
    ("# Negative Prompt\nblur\n# Positive Prompt\ncat", "cat", "blur"),
    ("```markdown\n# 正向提示词\n猫\n# 反向提示词\n模糊\n```", "猫", "模糊"),
    ("# 正向提示词\n```text\n猫\n```\n# 反向提示词\n模糊", "```text\n猫\n```", "模糊"),
    ("猫，白色背景。", "猫，白色背景。", ""),
    ("```text\n猫，白色背景。\n```", "猫，白色背景。", ""),
])
def test_extract_prompt_sections(doc, positive, negative):
    result = extract_prompt(doc)
    assert result.positive_prompt == positive
    assert result.negative_prompt == negative
    assert result.warning is None


@pytest.mark.parametrize("doc", [
    "前言也有具体约束\n# 正向提示词\n猫\n# 反向提示词\n模糊",
    "# 正向提示词\n猫\n# 补充\n尾巴要完整\n# 反向提示词\n模糊",
    "# 正向提示词\n猫\n# 正向提示词\n狗",
    "# 反向提示词\n模糊",
    "# 正向提示词\n## 反向提示词\n模糊",
    "# 正向提示词\n猫\n## 反向提示词\n模糊",
    "# 画面提示词\n猫\n# 排除内容\n模糊",
    "正向提示词：猫；反向提示词：模糊",
    "```markdown\n# 正向提示词\n猫\n# 其他内容\n尾巴要完整\n```",
])
def test_ambiguous_documents_preserve_original_for_editing(doc):
    result = extract_prompt(doc)
    assert result.positive_prompt == doc
    assert result.negative_prompt == ""
    assert result.warning


def test_projection_does_not_add_warning_to_generation_data():
    doc = "# 正向提示词\n猫\n# 补充\n尾巴完整"
    data = project_output(context(), "generation_engineer", doc, {})["data"]
    assert data["positive_prompt"] == doc
    assert "warning" not in data


def test_generic_section_does_not_change_other_projections():
    from app.workflow.markdown_handoff import section

    assert section("# 背景\n白色\n# 灯光\n柔光", "背景") == "白色"



@pytest.mark.parametrize("doc", [
    "正向：猫；反向：模糊", "Positive: cat; Negative: blur",
    "猫，反向提示词：不要模糊", "正向提示词：猫；负面提示词：模糊",
])
def test_ambiguous_inline_labels_warn_without_mixing_sections(doc):
    result = extract_prompt(doc)
    assert result.positive_prompt == doc
    assert result.negative_prompt == ""
    assert result.warning


def test_prompt_specific_alias_wins_over_generic_document_title():
    result = extract_prompt("# Prompt\n## Positive Prompt\ncat\n## Negative Prompt\nblur")
    assert result.positive_prompt == "cat"
    assert result.negative_prompt == "blur"
    assert result.warning is None


def test_setext_inline_label():
    assert extract_prompt("正向提示词：猫\n====").positive_prompt == "猫"


@pytest.mark.parametrize(("positive", "negative"), [("猫" * 12001, ""), ("猫", "糊" * 6001)])
def test_projection_keeps_length_limits(positive, negative):
    with pytest.raises(ApiError) as error:
        project_output(context(), "generation_engineer",
                       f"# 正向提示词\n{positive}\n# 反向提示词\n{negative}", {})
    assert error.value.code == "PROMPT_TOO_LONG"


@pytest.mark.parametrize("doc", [
    "Positive Prompt: **cat**", "**Positive Prompt**: **cat**",
    "**Positive Prompt:** **cat**", "__Positive Prompt:__ **cat**",
])
def test_inline_label_keeps_body_emphasis(doc):
    result = extract_prompt(doc)
    assert result.positive_prompt == "**cat**"
    assert result.warning is None


@pytest.mark.parametrize("doc", [
    "# 生成提示词\n## 正向提示词\n**主体**\n猫\n\n**场景**\n白色背景\n\n## 反向提示词\n**路径错误**\n不要断路",
    "# 正向提示词\n```text\n猫\n```\n\n保留中间的主体段落\n\n```text\n白色背景\n```\n\n最后一段保持猫尾完整\n# 反向提示词\n```text\n模糊\n```",
])
def test_complete_positive_body_survives_bold_subsections_and_multiple_fences(doc):
    result = extract_prompt(doc)
    negative_heading = "## 反向提示词" if "## 反向提示词" in doc else "# 反向提示词"
    expected_positive = doc.split("正向提示词\n", 1)[1].split(negative_heading)[0].strip()
    assert result.positive_prompt == expected_positive
    assert result.negative_prompt == doc.split(negative_heading, 1)[1].strip()
    assert result.warning is None


def test_multiple_fenced_sections_fall_back_without_dropping_either_body():
    doc = "```text\n正向提示词：猫\n```\n```text\n反向提示词：模糊\n```"
    result = extract_prompt(doc)
    assert result.positive_prompt == doc
    assert result.negative_prompt == ""
    assert result.warning


@pytest.mark.parametrize("doc", [
    "```markdown\n# 正向提示词\n猫\n# 反向提示词\n模糊",
    "# 正向提示词\n```text\n猫\n# 反向提示词\n模糊",
])
def test_unclosed_fences_preserve_original_with_warning(doc):
    result = extract_prompt(doc)
    assert result.positive_prompt == doc
    assert result.negative_prompt == ""
    assert result.warning
