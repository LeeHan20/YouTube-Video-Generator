from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.auth import require_admin
from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.services.review_service import ReviewService


router = APIRouter()


class RegenerateRequest(BaseModel):
    scene_id: str
    user_instruction: str
    source_mode: str = "crawl_image"


class CrawlCandidatesRequest(BaseModel):
    scene_ids: list[str]
    user_instruction: str = ""


class SelectCandidateRequest(BaseModel):
    scene_id: str
    candidate_id: str


def review_service() -> ReviewService:
    return ReviewService(SheetsRepository(SheetsClient()))


@router.get("/review/{session_id}", response_class=HTMLResponse)
def review_page(session_id: str, _: str = Depends(require_admin)) -> str:
    return REVIEW_HTML.replace("__SESSION_ID__", session_id)


@router.get("/api/review/{session_id}")
def get_review_session(
    session_id: str,
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict:
    try:
        return service.get_session_payload(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/review/{session_id}/regenerate-scene")
def regenerate_scene(
    session_id: str,
    payload: RegenerateRequest,
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict:
    try:
        return service.regenerate_scene(session_id, payload.scene_id, payload.user_instruction, payload.source_mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/review/{session_id}/crawl-image-candidates")
def crawl_image_candidates(
    session_id: str,
    payload: CrawlCandidatesRequest,
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict:
    try:
        return service.crawl_image_candidates(session_id, payload.scene_ids, payload.user_instruction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/review/{session_id}/select-image-candidate")
def select_image_candidate(
    session_id: str,
    payload: SelectCandidateRequest,
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict:
    try:
        return service.select_image_candidate(session_id, payload.scene_id, payload.candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/review/{session_id}/upload-scene-asset")
async def upload_scene_asset(
    session_id: str,
    scene_id: str = Form(...),
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict:
    try:
        return service.upload_scene_asset(session_id, scene_id, file.filename or "upload", await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/review/{session_id}/rerender")
def rerender(
    session_id: str,
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict:
    try:
        return service.rerender(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/review/{session_id}/approve")
def approve(
    session_id: str,
    _: str = Depends(require_admin),
    service: ReviewService = Depends(review_service),
) -> dict[str, str]:
    try:
        return service.approve(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


REVIEW_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Auto2 영상 편집</title>
  <style>
    * { box-sizing:border-box; }
    body { margin:0; font-family:Arial,sans-serif; background:#17191d; color:#edf1f5; overflow:hidden; }
    button, select, textarea, input { font:inherit; }
    button { border:0; border-radius:6px; padding:10px 12px; background:#2f6fed; color:white; font-weight:700; cursor:pointer; }
    button:disabled, input:disabled, select:disabled, textarea:disabled { opacity:.55; cursor:not-allowed; }
    button.secondary { background:#2d343d; color:#e7ebef; }
    button.approve { background:#3d7a43; }
    button.ghost { background:transparent; color:#dce3ea; border:1px solid #3a414d; }
    select, textarea, input { width:100%; border:1px solid #39414d; background:#111318; color:#edf1f5; border-radius:6px; padding:10px; }
    textarea { min-height:94px; resize:vertical; line-height:1.5; }
    .shell { height:100vh; display:grid; grid-template-rows:52px 1fr 210px; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:0 16px; background:#101215; border-bottom:1px solid #2a2f37; }
    .brand { display:flex; align-items:center; gap:12px; font-weight:800; }
    .dot { width:12px; height:12px; border-radius:999px; background:#48d17e; }
    .workspace { display:grid; grid-template-columns:280px minmax(420px,1fr) 360px; min-height:0; }
    .panel { min-height:0; overflow:auto; background:#1d2026; border-right:1px solid #2a2f37; }
    .panel.right { border-right:0; border-left:1px solid #2a2f37; }
    .panel h2 { font-size:15px; margin:0; padding:14px 14px 10px; color:#b8c2cc; }
    .asset-card, .scene-list-item { margin:10px 12px; border:1px solid #303743; border-radius:8px; background:#242832; padding:10px; cursor:pointer; }
    .asset-card.active, .scene-list-item.active { outline:2px solid #5b8cff; }
    .asset-thumb { height:96px; background:#0e1014; border-radius:6px; overflow:hidden; display:flex; align-items:center; justify-content:center; }
    .asset-thumb img, .asset-thumb video, .asset-thumb iframe { width:100%; height:100%; object-fit:cover; border:0; }
    .muted { color:#9aa6b2; font-size:13px; line-height:1.4; }
    .preview { min-height:0; display:grid; grid-template-rows:1fr auto; background:#14161b; }
    .viewer { min-height:0; padding:18px; display:flex; flex-direction:column; gap:12px; }
    .viewer-title { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
    .viewer-title h1 { font-size:20px; margin:0; line-height:1.35; }
    .stage { flex:1; min-height:260px; background:#050608; border-radius:10px; display:flex; align-items:center; justify-content:center; overflow:hidden; border:1px solid #2c333d; }
    .stage video, .stage iframe, .stage img { width:100%; height:100%; object-fit:contain; border:0; background:#050608; }
    .transport { display:flex; align-items:center; justify-content:space-between; padding:12px 18px; border-top:1px solid #2a2f37; background:#191c22; }
    .inspector { padding:14px; display:flex; flex-direction:column; gap:12px; }
    .field label { display:block; margin-bottom:6px; font-size:13px; color:#aeb8c3; font-weight:700; }
    .prompt { max-height:135px; overflow:auto; white-space:pre-wrap; background:#111318; border:1px solid #303743; border-radius:6px; padding:10px; color:#dfe6ee; line-height:1.45; }
    .candidate-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .candidate { background:#111318; border:1px solid #303743; border-radius:7px; padding:8px; display:flex; flex-direction:column; gap:6px; }
    .candidate.selected { outline:2px solid #6ca0ff; }
    .candidate img, .candidate video { width:100%; aspect-ratio:16/9; object-fit:contain; background:#050608; border-radius:5px; }
    .score { font-size:12px; color:#b7d0ff; font-weight:800; }
    .scene-check { width:auto; margin-right:6px; }
    .modal { position:fixed; inset:0; display:none; place-items:center; background:rgba(0,0,0,.58); z-index:20; }
    .modal.show { display:grid; }
    .modal-card { width:min(420px, calc(100vw - 32px)); background:#20242c; border:1px solid #424b59; border-radius:10px; padding:24px; text-align:center; box-shadow:0 20px 60px rgba(0,0,0,.45); }
    .modal-message { font-size:20px; font-weight:800; line-height:1.45; margin-bottom:18px; }
    .timeline { min-width:0; overflow:auto; background:#111318; border-top:1px solid #2a2f37; padding:14px; }
    .tracks { min-width:900px; display:grid; grid-template-rows:74px 48px 48px; gap:10px; }
    .track-label { color:#95a2af; font-size:12px; margin-bottom:6px; }
    .clips { display:flex; gap:8px; height:54px; }
    .clip { min-width:150px; max-width:260px; border-radius:7px; padding:8px; background:#2c405c; color:#f5f8fb; overflow:hidden; cursor:pointer; border:1px solid #416184; }
    .clip.active { outline:2px solid #6ca0ff; }
    .clip.audio { background:#3a4b2d; border-color:#55733f; }
    .clip.caption { background:#513a67; border-color:#70518d; }
    .clip-title { font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .clip-time { font-size:12px; opacity:.8; margin-top:4px; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    @media (max-width: 1100px) { body { overflow:auto; } .shell { height:auto; grid-template-rows:auto auto auto; } .workspace { grid-template-columns:1fr; } .panel { max-height:none; } }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><span class="dot"></span><span>Auto2 영상 편집</span></div>
      <div class="muted" id="status">불러오는 중...</div>
      <div><button class="secondary" id="rerenderBtn" onclick="rerender()">재렌더링</button> <button class="approve" id="approveBtn" onclick="approve()">최종 승인</button></div>
    </header>
    <main class="workspace">
      <aside class="panel">
        <h2>미디어 소스</h2>
        <div class="asset-card" onclick="setSourceMode('auto')"><strong>자동 선택</strong><div class="muted">장면 내용에 따라 AI 생성 또는 크롤링</div></div>
        <div class="asset-card" onclick="setSourceMode('ai_image')"><strong>AI 이미지 생성</strong><div class="muted">원본 생성 이미지, 저작권 리스크 최소화</div></div>
        <div class="asset-card active" onclick="setSourceMode('crawl_image')"><strong>이미지 크롤링</strong><div class="muted">라이선스 확인 가능한 공개 이미지 후보</div></div>
        <div class="asset-card" onclick="setSourceMode('crawl_video')"><strong>영상 크롤링</strong><div class="muted">초반 30초용 짧은 공개 영상 후보</div></div>
        <h2>장면 목록</h2>
        <div style="padding:0 12px 8px"><button class="secondary" id="batchCrawlBtn" onclick="crawlCheckedScenes()">선택한 장면 이미지 다시 가져오기</button></div>
        <div id="sceneList"></div>
      </aside>
      <section class="preview">
        <div class="viewer">
          <div class="viewer-title"><h1 id="title">불러오는 중...</h1><span class="muted" id="sceneMeta"></span></div>
          <div class="stage" id="stage"></div>
        </div>
        <div class="transport">
          <div class="muted" id="assetMeta">에셋 정보</div>
          <div><button class="ghost" onclick="openAsset()">원본 보기</button></div>
        </div>
      </section>
      <aside class="panel right">
        <h2>장면 속성</h2>
        <div class="inspector">
          <div class="field"><label>에셋 생성 방식</label><select id="sourceMode" onchange="setSourceMode(this.value)"><option value="auto">자동</option><option value="ai_image">AI 이미지 생성</option><option value="crawl_image">이미지 크롤링</option><option value="crawl_video">영상 크롤링</option></select></div>
          <div class="field"><label>수정 지시사항</label><textarea id="instruction" placeholder="예: 더 밝은 가족용 3D 느낌으로, 병원 이미지는 빼고 집에서 확인하는 장면으로"></textarea></div>
          <button id="regenerateBtn" onclick="crawlSelectedScene()">이 장면 이미지 다시 가져오기</button>
          <div class="field"><label>사용자 이미지/영상 업로드</label><input id="assetUpload" type="file" accept="image/*,video/*" onchange="uploadSelectedAsset()"></div>
          <div class="field"><label>크롤링 후보 이미지</label><div class="candidate-grid" id="candidateGrid"></div></div>
          <div class="field"><label>장면 프롬프트</label><div class="prompt" id="prompt"></div></div>
          <div class="field"><label>자막/나레이션</label><div class="prompt" id="subtitle"></div></div>
          <div class="field"><label>출처/라이선스</label><div class="prompt" id="credit"></div></div>
        </div>
      </aside>
    </main>
    <section class="timeline">
      <div class="track-label">비디오 트랙</div><div class="clips" id="videoTrack"></div>
      <div class="track-label">나레이션 트랙</div><div class="clips" id="audioTrack"></div>
      <div class="track-label">자막 트랙</div><div class="clips" id="captionTrack"></div>
    </section>
  </div>
  <div class="modal" id="modal"><div class="modal-card"><div class="modal-message" id="modalMessage"></div><div id="modalActions"></div></div></div>
  <script>
    const sessionId = "__SESSION_ID__";
    let data = null;
    let selectedSceneId = null;
    let sourceMode = "crawl_image";
    let busy = false;
    const auth = {credentials: "same-origin"};
    async function load() {
      const res = await fetch(`/api/review/${sessionId}`, auth);
      data = await res.json();
      selectedSceneId = data.manifest.scenes[0]?.scene_id;
      render();
    }
    function scene() { return data.manifest.scenes.find(item => item.scene_id === selectedSceneId) || data.manifest.scenes[0]; }
    function render() {
      const manifest = data.manifest;
      document.getElementById("title").textContent = manifest.title;
      document.getElementById("status").textContent = `${manifest.scenes.length}개 장면 · ${manifest.video_length_minutes || "-"}분`;
      document.getElementById("sceneList").innerHTML = manifest.scenes.map(item => `
        <div class="scene-list-item ${item.scene_id === selectedSceneId ? "active" : ""}" onclick="selectScene('${item.scene_id}')">
          <label onclick="event.stopPropagation()"><input class="scene-check" type="checkbox" value="${item.scene_id}">선택</label>
          <strong>${item.title}</strong><div class="muted">${Math.round(item.start_seconds)}초 · ${item.asset_source || "asset"}</div>
          <button class="secondary" onclick="event.stopPropagation(); crawlScene('${item.scene_id}')">이미지 다시 가져오기</button>
        </div>`).join("");
      renderSelected();
      renderTimeline();
    }
    function renderSelected() {
      const item = scene();
      if (!item) return;
      document.getElementById("sceneMeta").textContent = `${item.title} · ${Math.round(item.start_seconds)}초`;
      document.getElementById("stage").innerHTML = previewHtml(item.asset_url);
      document.getElementById("assetMeta").textContent = `${item.asset_source || "asset"} · ${item.asset_license || "license pending"}`;
      document.getElementById("prompt").textContent = item.visual_prompt || "";
      document.getElementById("subtitle").textContent = item.subtitle || item.narration || "";
      document.getElementById("credit").textContent = [item.asset_credit, item.asset_license].filter(Boolean).join("\\n") || "출처 정보 없음";
      document.getElementById("sourceMode").value = sourceMode;
      renderCandidates(item);
    }
    function previewHtml(url) {
      if (!url) return `<div class="muted">에셋 없음</div>`;
      const lower = url.toLowerCase();
      if (lower.endsWith(".mp4") || lower.endsWith(".webm")) return `<video controls src="${url}"></video>`;
      if (lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".png") || lower.endsWith(".svg") || lower.endsWith(".gif")) return `<img src="${url}" alt="">`;
      return `<iframe src="${url}"></iframe>`;
    }
    function renderTimeline() {
      const scenes = data.manifest.scenes;
      document.getElementById("videoTrack").innerHTML = scenes.map(clipHtml).join("");
      document.getElementById("audioTrack").innerHTML = scenes.map(item => `<div class="clip audio ${item.scene_id === selectedSceneId ? "active" : ""}" onclick="selectScene('${item.scene_id}')"><div class="clip-title">${item.title}</div><div class="clip-time">나레이션 ${Math.round(item.duration_seconds || 0)}초</div></div>`).join("");
      document.getElementById("captionTrack").innerHTML = scenes.map(item => `<div class="clip caption ${item.scene_id === selectedSceneId ? "active" : ""}" onclick="selectScene('${item.scene_id}')"><div class="clip-title">${item.subtitle || ""}</div><div class="clip-time">자막</div></div>`).join("");
    }
    function renderCandidates(item) {
      const candidates = item.image_candidates || [];
      const grid = document.getElementById("candidateGrid");
      if (!candidates.length) {
        grid.innerHTML = `<div class="muted">아직 후보 이미지가 없습니다.</div>`;
        return;
      }
      grid.innerHTML = candidates.map(candidate => `
        <div class="candidate ${candidate.candidate_id === item.selected_image_candidate ? "selected" : ""}">
          ${candidatePreview(candidate.asset_url)}
          <div class="score">적합도 ${candidate.score || 0}점</div>
          <div class="muted">${candidate.reason || ""}</div>
          <button onclick="selectCandidate('${item.scene_id}', '${candidate.candidate_id}')">이 이미지 사용</button>
        </div>`).join("");
    }
    function candidatePreview(url) {
      const lower = (url || "").toLowerCase();
      if (lower.endsWith(".mp4") || lower.endsWith(".webm")) return `<video src="${url}" muted></video>`;
      return `<img src="${url}" alt="">`;
    }
    function clipHtml(item) {
      return `<div class="clip ${item.scene_id === selectedSceneId ? "active" : ""}" onclick="selectScene('${item.scene_id}')"><div class="clip-title">${item.title}</div><div class="clip-time">${Math.round(item.start_seconds)}초 · ${item.asset_source || "asset"}</div></div>`;
    }
    function selectScene(sceneId) { selectedSceneId = sceneId; render(); }
    function setSourceMode(mode) {
      sourceMode = mode;
      document.querySelectorAll(".asset-card").forEach(card => card.classList.remove("active"));
      const order = ["auto","ai_image","crawl_image","crawl_video"];
      const idx = order.indexOf(mode);
      if (idx >= 0) document.querySelectorAll(".asset-card")[idx]?.classList.add("active");
      document.getElementById("sourceMode").value = mode;
    }
    async function crawlSelectedScene() {
      const item = scene();
      if (sourceMode === "ai_image") {
        await generateSelectedScene(item.scene_id);
        return;
      }
      await crawlScene(item.scene_id);
    }
    async function generateSelectedScene(sceneId) {
      if (busy) return;
      const note = document.getElementById("instruction").value;
      const btn = document.getElementById("regenerateBtn");
      setBusy(true, "AI 이미지 생성 중...");
      btn.textContent = "생성 중...";
      try {
        const res = await fetch(`/api/review/${sessionId}/regenerate-scene`, {
          method: "POST", headers: {"Content-Type":"application/json"},
          credentials: "same-origin",
          body: JSON.stringify({scene_id: sceneId, user_instruction: note, source_mode: "ai_image"})
        });
        data = await res.json(); render();
      } finally {
        btn.textContent = "이 장면 이미지 다시 가져오기";
        setBusy(false);
      }
    }
    async function crawlScene(sceneId) {
      await crawlScenes([sceneId]);
    }
    async function crawlCheckedScenes() {
      const ids = Array.from(document.querySelectorAll(".scene-check:checked")).map(item => item.value);
      await crawlScenes(ids);
    }
    async function crawlScenes(sceneIds) {
      if (busy || !sceneIds.length) return;
      const note = document.getElementById("instruction").value;
      const btn = document.getElementById("regenerateBtn");
      setBusy(true, "이미지 크롤링 중...");
      btn.textContent = "가져오는 중...";
      try {
        const res = await fetch(`/api/review/${sessionId}/crawl-image-candidates`, {
          method: "POST", headers: {"Content-Type":"application/json"},
          credentials: "same-origin",
          body: JSON.stringify({scene_ids: sceneIds, user_instruction: note})
        });
        data = await res.json(); render();
      } finally {
        btn.textContent = "이 장면 이미지 다시 가져오기";
        setBusy(false);
      }
    }
    async function selectCandidate(sceneId, candidateId) {
      if (busy) return;
      setBusy(true, "이미지 적용 중...");
      try {
        const res = await fetch(`/api/review/${sessionId}/select-image-candidate`, {
          method: "POST", headers: {"Content-Type":"application/json"},
          credentials: "same-origin",
          body: JSON.stringify({scene_id: sceneId, candidate_id: candidateId})
        });
        data = await res.json(); render();
      } finally {
        setBusy(false);
      }
    }
    async function uploadSelectedAsset() {
      const item = scene();
      const input = document.getElementById("assetUpload");
      if (!input.files.length) return;
      const form = new FormData();
      form.append("scene_id", item.scene_id);
      form.append("file", input.files[0]);
      setBusy(true, "업로드 이미지 적용 중...");
      try {
        const res = await fetch(`/api/review/${sessionId}/upload-scene-asset`, {method:"POST", credentials:"same-origin", body: form});
        data = await res.json(); render();
      } finally {
        input.value = "";
        setBusy(false);
      }
    }
    function openAsset() { const item = scene(); if (item?.asset_url) window.open(item.asset_url, "_blank"); }
    async function rerender() {
      if (!confirm("영상을 다시 생성하시겠습니까?")) return;
      showMessage("영상 렌더링 중입니다... 잠시만 기다려주세요.");
      setBusy(true);
      try {
        const res = await fetch(`/api/review/${sessionId}/rerender`, {method:"POST", credentials:"same-origin"});
        data = await res.json(); render();
        showMessage("영상 렌더링이 완료되었습니다.", true);
      } finally {
        setBusy(false);
      }
    }
    async function approve() {
      if (busy) return;
      const ok = await confirmModal("최종 승인하시겠습니까?");
      if (!ok) return;
      showMessage("최종 승인 중...");
      setBusy(true);
      try {
        await fetch(`/api/review/${sessionId}/approve`, {method:"POST", credentials:"same-origin"});
        showMessage("최종 승인되었습니다.");
        showMessage("영상 렌더링 중입니다... 잠시만 기다려주세요.");
        const res = await fetch(`/api/review/${sessionId}/rerender`, {method:"POST", credentials:"same-origin"});
        data = await res.json(); render();
        showMessage("영상 렌더링이 완료되었습니다.", true);
      } finally {
        setBusy(false);
      }
    }
    function setBusy(value, message) {
      busy = value;
      document.querySelectorAll("button,input,select,textarea").forEach(item => item.disabled = value);
      if (message) document.getElementById("status").textContent = message;
      if (!value && data) document.getElementById("status").textContent = `${data.manifest.scenes.length}개 장면 · ${data.manifest.video_length_minutes || "-"}분`;
    }
    function showMessage(message, autoClose=false) {
      document.getElementById("modalMessage").textContent = message;
      document.getElementById("modalActions").innerHTML = autoClose ? `<button onclick="hideModal()">확인</button>` : "";
      document.getElementById("modal").classList.add("show");
      if (autoClose) setTimeout(hideModal, 1800);
    }
    function hideModal() { document.getElementById("modal").classList.remove("show"); }
    function confirmModal(message) {
      document.getElementById("modalMessage").textContent = message;
      document.getElementById("modal").classList.add("show");
      return new Promise(resolve => {
        document.getElementById("modalActions").innerHTML = `<button id="confirmOk">확인</button> <button class="secondary" id="confirmCancel">취소</button>`;
        document.getElementById("confirmOk").onclick = () => { hideModal(); resolve(true); };
        document.getElementById("confirmCancel").onclick = () => { hideModal(); resolve(false); };
      });
    }
    load();
  </script>
</body>
</html>"""
