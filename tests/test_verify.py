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
    assert any("999.99" in m for m in verdicts[0].missing)


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
    assert any("16.85" in m for m in verdicts[0].missing)


def test_纯定性回答放行():
    """没有数字论断的回答不拦截——校验只管数字幻觉。"""
    passed, ratio, _ = verify_answer("该公司是新能源行业的代表性企业。[c1]", CHUNKS)
    assert passed is True
    assert ratio == 1.0


# ---- FR-7：边界匹配 / 单位归一 / 多引用 ----


def test_数字边界_5不命中56_4():
    """旧版子串匹配让编造的 5 命中证据里的 56.4——必须拒。"""
    answer = "公司营收增长 5%。[c1]"
    passed, _ratio, verdicts = verify_answer(answer, CHUNKS)
    assert passed is False
    assert any("5%" in m for m in verdicts[0].missing)


def test_数字边界_20不命中2025():
    answer = "公司收入 20亿元。[c1]"
    passed, _ratio, verdicts = verify_answer(answer, CHUNKS)
    assert passed is False
    assert any("20" in m for m in verdicts[0].missing)


def test_单位归一_亿元与万元等价():
    chunks = [_chunk("c1", "公司营业收入为5000万元。")]
    answer = "公司营业收入为0.5亿元。[c1]"
    passed, _ratio, verdicts = verify_answer(answer, chunks)
    assert passed is True, verdicts


def test_百分比不能拿金额顶替():
    chunks = [_chunk("c1", "公司营业收入为5亿元。")]
    answer = "公司毛利率为 5%。[c1]"
    passed, _ratio, _verdicts = verify_answer(answer, chunks)
    assert passed is False, "5% 与 5亿元 不是同一个数"


def test_数值写法差异_5与5_0等价():
    chunks = [_chunk("c1", "同比增长5.0%。")]
    answer = "同比增长 5%。[c1]"
    passed, _ratio, _verdicts = verify_answer(answer, chunks)
    assert passed is True


def test_多引用句_两个chunk都参与核对():
    """一句话引了 [c1][c2]：两个 chunk 的证据合起来都要能支撑它。"""
    answer = "公司营收378.43亿元，经营性现金流16.85亿元。[c1][c2]"
    passed, _ratio, verdicts = verify_answer(answer, CHUNKS)
    assert passed is True, verdicts
    assert verdicts[0].citation_id == "c1+c2"

    # c2 里没有 16.85 时，多引用也不能豁免
    wrong = [_chunk("c1", "公司2025年实现营业收入378.43亿元。"),
             _chunk("c2", "现金流状况良好。")]
    passed2, _r2, verdicts2 = verify_answer(answer, wrong)
    assert passed2 is False
    assert any("16.85" in m for m in verdicts2[0].missing)



def test_引用错块时自动补正确引用():
    """FR-10：答案算对但引错块（分业务表 c1 才是证据）。"""
    from app.rag.verify import repair_citations

    chunks = [
        _chunk("c1", "| 业务板块 | 2021年收入 | 占比 |\n| 乳制品 | 311.57亿元 | 36.41% |"),
        _chunk("c2", "2021年公司营业收入855.77亿元，同比增长21.92%。"),
    ]
    answer = "乳制品业务收入占营业收入的比重为36.41%。[c2]"
    fixed = repair_citations(answer, chunks)
    assert "[c1]" in fixed
    passed, _ratio, verdicts = verify_answer(fixed, chunks)
    assert passed and all(v.supported for v in verdicts)


def test_修复不了时绝不改判据():
    from app.rag.verify import repair_citations

    chunks = [_chunk("c1", "公司2025年实现营业收入378.43亿元。")]
    answer = "公司毛利率为99.99%。[c1]"
    fixed = repair_citations(answer, chunks)
    assert fixed == answer
    passed, _ratio, _verdicts = verify_answer(fixed, chunks)
    assert not passed


def test_数字相同但公司不同不得用于修复():
    from app.rag.verify import repair_citations, verify_answer

    chunks = [
        {"citation_id": "c1", "source": "新能源-000-甲公司-2025", "text": "甲公司2025年经营正常。"},
        {"citation_id": "c2", "source": "新能源-001-乙公司-2025", "text": "乙公司2025年营业收入100.00亿元。"},
    ]
    answer = "甲公司2025年营业收入100.00亿元。[c1]"
    fixed = repair_citations(answer, chunks, entities={"甲公司"})
    assert "[c2]" not in fixed
    passed, _ratio, _verdicts = verify_answer(fixed, chunks)
    assert not passed
