"""引用校验器单测：金融场景幻觉防线的行为契约。

形态：同步单测（校验是纯函数，不碰数据库/LLM）。
覆盖：引用归属、列表序号、无引用数字论断、幻觉数字、纯定性放行。
"""

from app.rag.verify import split_claims, verify_answer


def _chunk(cid: str, text: str) -> dict:
    return {"citation_id": cid, "source": "s", "section_path": "p", "text": text}


CHUNKS = [
    _chunk("c1", "公司2025年实现营业收入378.43亿元，同比增长56.4%。"),
    _chunk("c2", "经营性现金流净额16.85亿元，现金流状况良好。"),
]


def test_引用在句末也能正确归属():
    """LLM 习惯把引用放句尾——断句不能把引用和论断拆开。"""
    claims = split_claims(
        "公司2025年实现营业收入378.43亿元，同比增长56.4%。[c1]"
        "经营性现金流净额16.85亿元。[c2]"
    )
    by_cid = {cid: claim for claim, cid in claims}
    assert by_cid["c1"].startswith("公司2025年")
    assert by_cid["c2"] == "经营性现金流净额16.85亿元"


def test_列表序号不被当成数字论断():
    """"1. 宁德润能……" 的 1 是序号不是数据。"""
    claims = split_claims("根据研报：1. 宁德润能是代表性企业。2. 产品覆盖全产业链。")
    assert claims == []


def test_证据支持的论断通过校验():
    answer = "公司2025年实现营业收入378.43亿元，同比增长56.4%。[c1]"
    passed, ratio, _verdicts = verify_answer(answer, CHUNKS)
    assert passed is True
    assert ratio == 1.0


def test_编造的数字被拒():
    """幻觉形态：数字根本不在被引 chunk 里——必须拒。"""
    answer = "公司2025年实现营业收入999.99亿元，远超行业平均。[c1]"
    passed, _ratio, verdicts = verify_answer(answer, CHUNKS)
    assert passed is False
    assert "999.99" in verdicts[0].missing


def test_无引用的数字论断被拒():
    """另一种幻觉：有数字但不给引用——同样拒。"""
    answer = "公司2025年实现营业收入378.43亿元。"
    passed, _, verdicts = verify_answer(answer, CHUNKS)
    assert passed is False
    assert verdicts[0].citation_id == ""


def test_引用指向错误的chunk被拒():
    """数字在语料里但引用标错块——也是不忠实引用。"""
    answer = "经营性现金流净额16.85亿元。[c1]"
    passed, _, verdicts = verify_answer(answer, CHUNKS)
    assert passed is False
    assert "16.85" in verdicts[0].missing


def test_纯定性回答放行():
    """没有数字论断的回答不拦截——校验只管数字幻觉。"""
    passed, ratio, _ = verify_answer("该公司是新能源行业的代表性企业。[c1]", CHUNKS)
    assert passed is True
    assert ratio == 1.0
