// CHAPTER JOB ORCHESTRATION — ADAPTED FROM xianslate's translation-service.ts (SAME JOB MAP +
// BUFFERED-EVENTS + SUPERSEDE PATTERN), BUT GENERIC: THE CALLER SUPPLIES THE ACTUAL WORK (THE
// PER-PAGE PIPELINE LANDS IN PHASE 5 — THE SERVICE OWNS THE LIFECYCLE, NOT THE PIPELINE).
//
// CONTRACT (PINS THE SSE CONTRACT THE UI DEPENDS ON):
//   - ONE DETACHED JOB PER CHAPTER KEY; startChapterJob IS IDEMPOTENT (ATTACHES TO A RUNNING JOB).
//   - force: TRUE ABORTS THE RUNNING JOB AND STARTS FRESH (SUPERSEDE — THE READER RE-RAN).
//   - EVENTS ARE BUFFERED AND REPLAYED TO (RE)CONNECTING SUBSCRIBERS — SSE RESUMPTION FOR FREE.
//   - ABORT SIGNALS FLOW TO THE WORK FUNCTION VIA AbortController.
import type { TranslationUsage } from '$lib/types';

// -- TYPES -- //

export type JobStatus = 'running' | 'done' | 'failed' | 'superseded';

export interface JobEvent {
	type: 'start' | 'page-done' | 'usage' | 'done' | 'error';
	/** 0-BASED PAGE INDEX (FOR progress DISPLAY) */
	page?: number;
	pageCount?: number;
	usage?: TranslationUsage;
	message?: string;
}

export interface JobHandle {
	key: string;
	status: JobStatus;
	/** SUBSCRIBE TO FUTURE EVENTS; IMMEDIATELY REPLAYS THE BUFFERED ONES. RETURNS AN UNSUBSCRIBE FN. */
	subscribe(fn: (e: JobEvent) => void): () => void;
	abort(): void;
}

export interface ChapterJobWork {
	(signal: AbortSignal, emit: (e: JobEvent) => void): Promise<void>;
}

// -- INTERNALS -- //

interface Job {
	key: string;
	status: JobStatus;
	controller: AbortController;
	events: JobEvent[];
	listeners: Set<(e: JobEvent) => void>;
}

// PROCESS-WIDE JOB REGISTRY — ONE JOB PER CHAPTER (SINGLE-INSTANCE APP).
const jobs = new Map<string, Job>();

function emit(job: Job, event: JobEvent): void {
	job.events.push(event);
	for (const fn of job.listeners) fn(event);
}

async function run(key: string, work: ChapterJobWork, initial: JobEvent[]): Promise<void> {
	const job = jobs.get(key);
	if (!job) return;
	for (const e of initial) emit(job, e);
	try {
		await work(job.controller.signal, (e) => emit(job, e));
		if (job.status === 'running') {
			job.status = 'done';
			emit(job, { type: 'done' });
		}
	} catch (e) {
		if (job.status !== 'superseded') {
			job.status = 'failed';
			emit(job, { type: 'error', message: e instanceof Error ? e.message : String(e) });
		}
	} finally {
		job.listeners.clear();
		jobs.delete(key);
	}
}

// -- PUBLIC API -- //

export function startChapterJob(chapterId: number, work: ChapterJobWork, opts: { force?: boolean } = {}): JobHandle {
	const key = `chapter:${chapterId}`;
	const existing = jobs.get(key);
	if (existing && existing.status === 'running') {
		if (!opts.force) return toHandle(existing);
		// SUPERSEDE — THE NEW RUN REPLACES THE OLD ONE
		existing.status = 'superseded';
		existing.controller.abort();
	}
	const job: Job = {
		key,
		status: 'running',
		controller: new AbortController(),
		events: [],
		listeners: new Set(),
	};
	jobs.set(key, job);
	void run(key, work, [{ type: 'start' }]);
	return toHandle(job);
}

export function getChapterJob(chapterId: number): JobHandle | null {
	const job = jobs.get(`chapter:${chapterId}`);
	return job ? toHandle(job) : null;
}

function toHandle(job: Job): JobHandle {
	return {
		key: job.key,
		// A GETTER — THE STATUS MUST REFLECT THE LIVE JOB, NOT THE VALUE AT HANDLE-CREATION TIME
		get status() {
			return job.status;
		},
		subscribe(fn) {
			job.listeners.add(fn);
			// REPLAY THE BUFFER — A (RE)CONNECTING SSE CLIENT SEES EVERYTHING THAT HAPPENED SO FAR
			for (const e of job.events) fn(e);
			return () => job.listeners.delete(fn);
		},
		abort() {
			job.controller.abort();
		},
	};
}
