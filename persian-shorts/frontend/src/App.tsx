import { useEffect, useRef, useState } from "react";
import { createJob, getJob } from "./api";
import type { JobState, JobStatus } from "./types";
import "./App.css";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "در صف انتظار...",
  downloading: "در حال دانلود ویدیو...",
  transcribing: "در حال تبدیل گفتار فارسی به متن...",
  finding_highlights: "در حال پیدا کردن جذاب‌ترین لحظات...",
  clipping: "در حال برش و قاب‌بندی عمودی کلیپ‌ها...",
  done: "تمام شد!",
  failed: "خطا رخ داد",
};

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function App() {
  const [url, setUrl] = useState("");
  const [maxClips, setMaxClips] = useState(5);
  const [job, setJob] = useState<JobState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  function startPolling(jobId: string) {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const updated = await getJob(jobId);
        setJob(updated);
        if (updated.status === "done" || updated.status === "failed") {
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      } catch (err) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        setFormError(err instanceof Error ? err.message : String(err));
      }
    }, 2000);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!url.trim()) return;
    setSubmitting(true);
    try {
      const created = await createJob(url.trim(), maxClips);
      setJob(created);
      startPolling(created.id);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const isRunning = job && job.status !== "done" && job.status !== "failed";

  return (
    <div className="page">
      <header className="header">
        <h1>کوتاه‌ساز</h1>
        <p className="subtitle">
          لینک ویدیوی یوتیوب فارسی خود را بچسبانید، هوش مصنوعی جذاب‌ترین لحظاتش را پیدا
          می‌کند و آن‌ها را به کلیپ‌های کوتاه و عمودی تبدیل می‌کند.
        </p>
      </header>

      <form className="job-form" onSubmit={handleSubmit}>
        <input
          type="url"
          required
          dir="ltr"
          placeholder="https://www.youtube.com/watch?v=..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={Boolean(isRunning) || submitting}
        />
        <label className="max-clips">
          تعداد کلیپ:
          <input
            type="number"
            min={1}
            max={10}
            value={maxClips}
            onChange={(e) => setMaxClips(Number(e.target.value))}
            disabled={Boolean(isRunning) || submitting}
          />
        </label>
        <button type="submit" disabled={Boolean(isRunning) || submitting}>
          {submitting ? "در حال ارسال..." : "ساخت کلیپ‌ها"}
        </button>
      </form>

      {formError && <p className="error">{formError}</p>}

      {job && (
        <section className="job-status">
          <div className="progress-bar">
            <div
              className={`progress-fill ${job.status === "failed" ? "failed" : ""}`}
              style={{ width: `${Math.round(job.progress * 100)}%` }}
            />
          </div>
          <p className="status-message">
            {STATUS_LABELS[job.status]}
            {job.source_title ? ` — ${job.source_title}` : ""}
          </p>
          {job.error && <p className="error">{job.error}</p>}
        </section>
      )}

      {job && job.clips.length > 0 && (
        <section className="clips-grid">
          {job.clips.map((clip) => (
            <article className="clip-card" key={clip.id}>
              <video controls src={clip.download_url} preload="metadata" />
              <h3>{clip.title}</h3>
              <p className="clip-reason">{clip.reason}</p>
              <p className="clip-time" dir="ltr">
                {formatTime(clip.start)} – {formatTime(clip.end)}
              </p>
              <a className="download-btn" href={clip.download_url} download>
                دانلود کلیپ
              </a>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
