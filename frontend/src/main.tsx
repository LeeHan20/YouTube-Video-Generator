import React, { ChangeEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Check, Image, RefreshCw, Search, Sparkles, Wand2 } from "lucide-react";
import "./styles.css";

type SourceMode = "auto" | "ai_image" | "crawl_image" | "crawl_video";

type Scene = {
  scene_id: string;
  title: string;
  subtitle: string;
  narration: string;
  visual_prompt: string;
  asset_url: string;
  asset_source: string;
  asset_credit: string;
  asset_license: string;
  start_seconds: number;
  duration_seconds: number;
};

type Manifest = {
  title: string;
  video_url: string;
  video_length_minutes: number;
  scenes: Scene[];
};

type Payload = { manifest: Manifest };

function App() {
  const sessionId = useMemo(() => new URLSearchParams(location.search).get("session") || location.pathname.split("/").pop(), []);
  const [payload, setPayload] = useState<Payload | null>(null);
  const [selectedSceneId, setSelectedSceneId] = useState<string>("");
  const [sourceMode, setSourceMode] = useState<SourceMode>("auto");
  const [instruction, setInstruction] = useState("");

  async function load() {
    const res = await fetch(`/api/review/${sessionId}`, { credentials: "same-origin" });
    const nextPayload = await res.json();
    setPayload(nextPayload);
    setSelectedSceneId(nextPayload.manifest.scenes[0]?.scene_id || "");
  }

  async function regenerate() {
    if (!selectedScene) return;
    const res = await fetch(`/api/review/${sessionId}/regenerate-scene`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene_id: selectedScene.scene_id, user_instruction: instruction, source_mode: sourceMode })
    });
    setPayload(await res.json());
  }

  async function rerender() {
    const res = await fetch(`/api/review/${sessionId}/rerender`, { method: "POST", credentials: "same-origin" });
    setPayload(await res.json());
  }

  async function approve() {
    await fetch(`/api/review/${sessionId}/approve`, { method: "POST", credentials: "same-origin" });
    alert("승인되었습니다.");
  }

  useEffect(() => { load(); }, []);

  const selectedScene = payload?.manifest.scenes.find(scene => scene.scene_id === selectedSceneId) || payload?.manifest.scenes[0];

  if (!payload || !selectedScene) return <main className="loading">불러오는 중...</main>;

  return (
    <main className="editor">
      <header className="topbar">
        <strong>Auto2 영상 편집</strong>
        <span>{payload.manifest.scenes.length}개 장면 · {payload.manifest.video_length_minutes}분</span>
        <div className="bar-actions">
          <button className="secondary" onClick={rerender}><RefreshCw size={18} /> 재렌더링</button>
          <button className="approve" onClick={approve}><Check size={18} /> 최종 승인</button>
        </div>
      </header>
      <section className="workspace">
        <aside className="media-panel">
          <h2>미디어 소스</h2>
          <SourceButton mode="auto" active={sourceMode === "auto"} onClick={setSourceMode} icon={<Sparkles size={18} />} title="자동 선택" />
          <SourceButton mode="ai_image" active={sourceMode === "ai_image"} onClick={setSourceMode} icon={<Wand2 size={18} />} title="AI 이미지 생성" />
          <SourceButton mode="crawl_image" active={sourceMode === "crawl_image"} onClick={setSourceMode} icon={<Image size={18} />} title="이미지 크롤링" />
          <SourceButton mode="crawl_video" active={sourceMode === "crawl_video"} onClick={setSourceMode} icon={<Search size={18} />} title="영상 크롤링" />
          <h2>장면 목록</h2>
          {payload.manifest.scenes.map(scene => (
            <button key={scene.scene_id} className={`scene-nav ${scene.scene_id === selectedScene.scene_id ? "active" : ""}`} onClick={() => setSelectedSceneId(scene.scene_id)}>
              <strong>{scene.title}</strong>
              <span>{Math.round(scene.start_seconds)}초 · {scene.asset_source || "asset"}</span>
            </button>
          ))}
        </aside>
        <section className="viewer">
          <h1>{payload.manifest.title}</h1>
          <div className="stage">{preview(selectedScene.asset_url)}</div>
          <div className="asset-meta">{selectedScene.asset_source} · {selectedScene.asset_license || "license pending"}</div>
        </section>
        <aside className="inspector">
          <h2>장면 속성</h2>
          <label>생성 방식</label>
          <select value={sourceMode} onChange={event => setSourceMode(event.target.value as SourceMode)}>
            <option value="auto">자동</option>
            <option value="ai_image">AI 이미지 생성</option>
            <option value="crawl_image">이미지 크롤링</option>
            <option value="crawl_video">영상 크롤링</option>
          </select>
          <label>수정 지시사항</label>
          <textarea value={instruction} onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setInstruction(event.target.value)} />
          <button onClick={regenerate}><Wand2 size={18} /> 이 장면 다시 생성</button>
          <label>프롬프트</label>
          <pre>{selectedScene.visual_prompt}</pre>
          <label>자막</label>
          <pre>{selectedScene.subtitle || selectedScene.narration}</pre>
          <label>출처</label>
          <pre>{[selectedScene.asset_credit, selectedScene.asset_license].filter(Boolean).join("\n") || "출처 정보 없음"}</pre>
        </aside>
      </section>
      <section className="timeline">
        <Track label="비디오" scenes={payload.manifest.scenes} selected={selectedScene.scene_id} onSelect={setSelectedSceneId} />
        <Track label="나레이션" scenes={payload.manifest.scenes} selected={selectedScene.scene_id} onSelect={setSelectedSceneId} variant="audio" />
        <Track label="자막" scenes={payload.manifest.scenes} selected={selectedScene.scene_id} onSelect={setSelectedSceneId} variant="caption" />
      </section>
    </main>
  );
}

function SourceButton(props: { mode: SourceMode; active: boolean; title: string; icon: React.ReactNode; onClick: (mode: SourceMode) => void }) {
  return <button className={`source ${props.active ? "active" : ""}`} onClick={() => props.onClick(props.mode)}>{props.icon}<span>{props.title}</span></button>;
}

function Track(props: { label: string; scenes: Scene[]; selected: string; onSelect: (id: string) => void; variant?: string }) {
  return (
    <div>
      <div className="track-label">{props.label}</div>
      <div className="clips">
        {props.scenes.map(scene => (
          <button key={scene.scene_id} className={`clip ${props.variant || ""} ${scene.scene_id === props.selected ? "active" : ""}`} onClick={() => props.onSelect(scene.scene_id)}>
            <strong>{scene.title}</strong><span>{Math.round(scene.start_seconds)}초</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function preview(url: string) {
  const lower = url.toLowerCase();
  if (lower.endsWith(".mp4") || lower.endsWith(".webm")) return <video controls src={url} />;
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".png") || lower.endsWith(".svg") || lower.endsWith(".gif")) return <img src={url} />;
  return <iframe src={url} />;
}

createRoot(document.getElementById("root")!).render(<App />);
