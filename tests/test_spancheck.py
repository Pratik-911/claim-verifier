from verifier.spancheck import span_results

REAL = {"claim_id": "C3", "evidence_page": 13, "evidence_source": "LixiLan-O_Rosenstock2016 (text_layer)",
        "evidence_span": "changes from baseline to Week 30 in HbA1c were −1.63% for iGlarLixi, −1.34% for"}
OCR = {"claim_id": "C5", "evidence_page": 2, "evidence_source": "LixiLan-L_Aroda2016_abstract (ocr)",
       "evidence_span": "Mean body weight decreased by 0.7 kg with iGlarLixi and increased by"}
FAKE = {"claim_id": "X", "evidence_page": 13, "evidence_source": "LixiLan-O_Rosenstock2016 (text_layer)",
        "evidence_span": "weight neutral in every patient"}
NONE = {"claim_id": "Y", "evidence_page": None, "evidence_source": None, "evidence_span": None}


def test_real_text_layer_span_is_located_and_confirmed_by_pypdf():
    (r,) = span_results([REAL])
    assert r["located"] and r["source_kind"] == "text_layer" and r["page_ok"] and r["pypdf"] is True


def test_ocr_span_is_located_and_labelled():
    (r,) = span_results([OCR])
    assert r["located"] and r["source_kind"] == "ocr" and r["pypdf"] is None


def test_invented_span_fails():
    (r,) = span_results([FAKE])
    assert not r["located"]


def test_missing_span_is_skipped():
    (r,) = span_results([NONE])
    assert r["located"] is None


SPLIT_WORD = {"claim_id": "C4", "evidence_page": 6, "evidence_source": "SoliMix_Rosenstock2021 (text_layer)",
              "evidence_span": "The percentage of partic-\nipants who reached HbA1c target <7%"}


def test_second_extractor_check_is_whitespace_tolerant():
    # pypdf prints "HbA 1c" (font change at the subscript); the brief's checker ignores whitespace
    (r,) = span_results([SPLIT_WORD])
    assert r["located"] and r["pypdf"] is True


def test_whitespace_tolerance_does_not_accept_different_words():
    wrong = dict(SPLIT_WORD, evidence_span="The percentage of partic-\nipants who reached HbA1c target <6.5%")
    (r,) = span_results([wrong])
    assert not r["located"]
