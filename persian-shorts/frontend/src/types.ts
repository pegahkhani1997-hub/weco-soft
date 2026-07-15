export type JobStatus =
  | "queued"
  | "downloading"
  | "transcribing"
  | "finding_highlights"
  | "clipping"
  | "done"
  | "failed";

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

export interface Clip {
  id: string;
  title: string;
  reason: string;
  start: number;
  end: number;
  file_path: string;
  download_url: string;
}

export interface JobState {
  id: string;
  youtube_url: string;
  status: JobStatus;
  progress: number;
  message: string;
  error: string | null;
  source_title: string | null;
  transcript: TranscriptSegment[];
  highlights: unknown[];
  clips: Clip[];
}
