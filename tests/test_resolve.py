from verifier.resolve import Materials, resolve_citation

DOCS = [
    {"doc_id": "SoliMix_Rosenstock2021", "path": "papers/SoliMix_Rosenstock2021.pdf", "doi": "10.2337/dc21-0393",
     "title": "Advancing Therapy in Suboptimally Controlled Basal Insulin-Treated Type 2 Diabetes: Clinical "
              "Outcomes With iGlarLixi Versus Premix BIAsp 30 in the SoliMix Randomized Controlled Trial",
     "year": 2021, "aliases": ["SoliMix"]},
    {"doc_id": "LixiLan-L_Aroda2016_abstract", "path": "papers/LixiLan-L_Aroda2016_abstract.pdf",
     "doi": "10.2337/dc16-1495", "year": 2016, "aliases": ["LixiLan-L"],
     "title": "Efficacy and Safety of LixiLan in Type 2 Diabetes Inadequately Controlled on Basal Insulin and "
              "Metformin: The LixiLan-L Randomized Trial"},
    {"doc_id": "Tabak2020_PSM", "path": "papers/Tabak2020_PSM.pdf", "doi": "10.1007/s13300-019-00735-7",
     "year": 2020, "aliases": ["Tabák 2020", "propensity-score-matching analysis"],
     "title": "Efficacy and Safety of iGlarLixi Compared with Basal-Bolus Regimen in Patients with Type 2 "
              "Diabetes: Propensity Score Matched Analysis"},
    {"doc_id": "SOLIQUA_LBL", "path": "papers/SOLIQUA_LBL.pdf", "aliases": ["SOLIQUA LBL"], "title": ""},
]
M = Materials(documents=DOCS)


def test_explicit_filename():
    r = resolve_citation("papers/SoliMix_Rosenstock2021.pdf", M)
    assert r.doc_ids == ["SoliMix_Rosenstock2021"] and r.resolved


def test_filename_with_page_hint():
    r = resolve_citation("SOLIQUA_LBL.pdf (Prescribing Information, page 4)", M)
    assert r.doc_ids == ["SOLIQUA_LBL"]
    assert r.page_hints == {"SOLIQUA_LBL": 4}


def test_filename_plus_aliases_in_order_of_mention():
    r = resolve_citation("papers/Tabak2020_PSM.pdf (plus LixiLan-L abstract and SoliMix)", M)
    assert r.doc_ids == ["Tabak2020_PSM", "LixiLan-L_Aroda2016_abstract", "SoliMix_Rosenstock2021"]


def test_doi_identifies_article():
    assert resolve_citation("doi:10.2337/dc16-1495", M).doc_ids == ["LixiLan-L_Aroda2016_abstract"]


def test_unknown_file_is_unresolved_not_guessed():
    r = resolve_citation("papers/Unknown_Trial_2019.pdf", M)
    assert not r.resolved and r.doc_ids == []
    assert r.unresolved == ["Unknown_Trial_2019.pdf"]


def test_alias_shared_by_two_documents_is_ambiguous():
    m = Materials(documents=[{**DOCS[0], "aliases": ["Rosenstock"]},
                             {**DOCS[1], "doc_id": "LixiLan-O", "path": "papers/LixiLan-O.pdf", "aliases": ["Rosenstock"]}])
    r = resolve_citation("Rosenstock et al.", m)
    assert not r.resolved
    assert sorted(r.ambiguous) == ["LixiLan-O", "SoliMix_Rosenstock2021"]


def test_fuzzy_title_needs_threshold_and_margin():
    r = resolve_citation("iGlarLixi compared with basal-bolus regimen: propensity score matched analysis (2020)", M)
    assert r.doc_ids == ["Tabak2020_PSM"]


def test_weak_fuzzy_match_is_not_forced():
    r = resolve_citation("A randomised trial of metformin in obesity, 2019", M)
    assert not r.resolved and r.doc_ids == []


def test_fuzzy_year_must_be_compatible():
    r = resolve_citation("iGlarLixi compared with basal-bolus regimen: propensity score matched analysis (2015)", M)
    assert not r.resolved
