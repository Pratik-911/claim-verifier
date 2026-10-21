"""Smoke test of the Label Studio Community page-image workflow (plan §12.2 step 3).

Round trip: render 5 PDF pages -> start Label Studio -> create project (rectangles + per-region quote +
relation) -> import pages -> create one region annotation via the API -> export JSON -> convert to
annotation-v1 evidence -> locate the quote in the source -> validate. Exit 0 only if every step works.

Usage: python scripts/labelstudio_smoke.py   (needs .ls-venv with label-studio installed)
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from scripts.labelstudio_convert import convert_task  # noqa: E402
from verifier.annotation import validate_record  # noqa: E402

PAGES = [("SoliMix_Rosenstock2021", 5), ("SoliMix_Rosenstock2021", 6), ("SoliMix_Rosenstock2021", 7),
         ("SOLIQUA_LBL", 3), ("LixiLan-L_Aroda2016_abstract", 2)]
CONFIG = """<View>
  <Image name="page" value="$image" zoom="true"/>
  <RectangleLabels name="region" toName="page">
    <Label value="Evidence"/><Label value="Number"/><Label value="ArmLabel"/>
  </RectangleLabels>
  <TextArea name="quote" toName="page" perRegion="true" editable="true" placeholder="verbatim quote"/>
  <Choices name="relation" toName="page" perRegion="true">
    <Choice value="entailed"/><Choice value="contradicted"/><Choice value="unknown"/>
  </Choices>
</View>"""
PORT = 8089


def api(method: str, path: str, token: str, body=None, raw=False):
    req = urllib.request.Request(f"http://localhost:{PORT}{path}", method=method,
                                 headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data or b"null")


def upload(project_id: int, token: str, files: list[Path]) -> None:
    boundary = secrets.token_hex(8)
    parts = []
    for i, f in enumerate(files):  # distinct field names: Django keeps only the last value per name
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file{i}\"; filename=\"{f.name}\"\r\n"
                     f"Content-Type: image/png\r\n\r\n".encode() + f.read_bytes() + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"http://localhost:{PORT}/api/projects/{project_id}/import", method="POST", data=body,
                                 headers={"Authorization": f"Token {token}",
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=120).read()


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="ls-smoke-"))
    images, page_meta = [], {}
    for doc_id, page_no in PAGES:
        with pymupdf.open(ROOT / "papers" / f"{doc_id}.pdf") as pdf:
            page = pdf[page_no - 1]
            out = work / f"{doc_id}__p{page_no}.png"
            page.get_pixmap(dpi=110).save(out)
            images.append(out)
            page_meta[out.name] = (doc_id, page_no, page.rect.width, page.rect.height)
    token = secrets.token_hex(20)
    env = {**os.environ, "LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK": "true",
           "LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN": "true"}  # the smoke test uses a fixed local token
    server = subprocess.Popen([str(ROOT / ".ls-venv/bin/label-studio"), "start", "--no-browser", "--port", str(PORT),
                               "--data-dir", str(work / "ls-data"), "--username", "pilot@example.org",
                               "--password", secrets.token_hex(12), "--user-token", token],
                              stdout=open(work / "server.log", "w"), stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(180):  # wait up to ~3 minutes for first-run migrations
            try:
                urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            print("FAIL: server did not start; see", work / "server.log")
            return 1
        print("1 server up")
        project = api("POST", "/api/projects", token, {"title": "pilot-smoke", "label_config": CONFIG})
        print("2 project", project["id"])
        upload(project["id"], token, images)
        tasks = api("GET", f"/api/projects/{project['id']}/tasks?page_size=50", token)
        tasks = tasks["tasks"] if isinstance(tasks, dict) else tasks
        print("3 imported tasks", len(tasks))
        target = next(t for t in tasks if "SoliMix_Rosenstock2021__p7" in json.dumps(t["data"]))
        rid = "r1"
        result = [
            {"id": rid, "from_name": "region", "to_name": "page", "type": "rectanglelabels",
             "value": {"x": 40, "y": 30, "width": 25, "height": 5, "rotation": 0, "rectanglelabels": ["Evidence"]}},
            {"id": rid, "from_name": "quote", "to_name": "page", "type": "textarea",
             "value": {"x": 40, "y": 30, "width": 25, "height": 5, "rotation": 0, "text": ["6.3 (n=28) 0.45 (0.28, 0.73)"]}},
            {"id": rid, "from_name": "relation", "to_name": "page", "type": "choices",
             "value": {"x": 40, "y": 30, "width": 25, "height": 5, "rotation": 0, "choices": ["entailed"]}},
        ]
        api("POST", f"/api/tasks/{target['id']}/annotations", token, {"result": result})
        print("4 annotation created on task", target["id"])
        exported = json.loads(api("GET", f"/api/projects/{project['id']}/export?exportType=JSON", token, raw=True))
        annotated = [t for t in exported if t.get("annotations")]
        print("5 exported tasks with annotations", len(annotated))
        evidence = convert_task(annotated[0], page_meta, ROOT)
        print("6 converted evidence", json.dumps(evidence, indent=1)[:600])
        assert evidence and evidence[0]["located"], "quote not located in source"
        rec = {"schema_version": "annotation-v1",
               "claim": {"claim_id": "SMOKE", "text": "Lower level-2 hypoglycemia OR 0.45 (0.28, 0.73)"},
               "scope": "claim_only", "source_stratum": "assignment_style", "family_ids": ["SoliMix"],
               "assertions": [{"assertion_id": "A1", "origin": "text", "origin_ref": "OR 0.45 (0.28, 0.73)",
                               "statement": "OR 0.45 (0.28, 0.73)", "check_category": "figure",
                               "corpus_relation": evidence[0]["relation"], "corpus_flags": []}],
               "evidence": [{k: v for k, v in evidence[0].items() if k not in ("located", "relation")}],
               "sufficient_evidence_sets": [[evidence[0]["evidence_id"]]], "corpus_verdict": "supported",
               "annotator": "smoke", "annotator_role": "annotator", "independent": True,
               "model_prelabels_shown": False, "seconds_spent": 0}
        problems = validate_record(rec)
        print("7 validation", problems or "ok")
        return 0 if not problems else 1
    finally:
        server.terminate()
        server.wait(timeout=30)


if __name__ == "__main__":
    sys.exit(main())
