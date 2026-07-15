import type { JobState } from "./types";

const API_BASE = "/api";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `درخواست ناموفق بود (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export function createJob(youtubeUrl: string, maxClips?: number): Promise<JobState> {
  return fetch(`${API_BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ youtube_url: youtubeUrl, max_clips: maxClips }),
  }).then((res) => asJson<JobState>(res));
}

export function getJob(jobId: string): Promise<JobState> {
  return fetch(`${API_BASE}/jobs/${jobId}`).then((res) => asJson<JobState>(res));
}
