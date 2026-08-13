// START (OR ATTACH TO) A CHAPTER TRANSLATION JOB — RESPONDS WITH AN SSE STREAM OF JOB EVENTS.
//
// POST /api/chapters/[id]/translate  body: {"force": boolean}
//   events: data: {"type":"start"|"page-done"|"error"|"done", ...}\n\n
//
// THE JOB IS DETACHED AND BUFFERED (translation-service) — A CLIENT DISCONNECT DOES NOT KILL IT,
// AND A (RE)CONNECTING CLIENT REPLAYS EVERYTHING SO FAR. THE STREAM CLOSES ON done/error.
// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { z } from 'zod';
// IMPORTED ENVS ($env/...)
import { env } from '$env/dynamic/private';
// IMPORTED MODULES
import { assertChapterExists } from '$lib/server/chapters';
import { chapterWork } from '$lib/server/chapter-pipeline';
import { createPipelineClient } from '$lib/server/pipeline-client';
import { DATA_ROOT } from '$lib/server/paths';
import { aiUsage } from '$lib/server/db/schema';
import { db } from '$lib/server/db';
import { startChapterJob } from '$lib/server/translation-service';
import type { RequestHandler } from './$types';

const Body = z.object({
	force: z.boolean().default(false),
});

export const POST: RequestHandler = async ({ params, request }) => {
	const chapterId = Number(params.id);
	if (!Number.isInteger(chapterId)) throw error(400, 'Invalid chapter id.');
	await assertChapterExists(chapterId);

	const parsed = Body.safeParse(await request.json().catch(() => null));
	const force = parsed.success ? parsed.data.force : false;

	// RECORD AI SPEND ON THE LEDGER (THE JOB STAYS DETACHED — FAILURES LOG, NOT THROW)
	const deps = {
		pipeline: createPipelineClient(),
		dataRoot: DATA_ROOT,
		// THE CACHE MUST NEVER MIX PROVIDERS: MOCK ↔ REAL SWITCHES PRODUCE A FRESH KEY
		cacheSalt: env.DEEPSEEK_BASE_URL ?? '',
		onUsage: (u: { model: string; promptTokens: number; cachedTokens: number; completionTokens: number; costUsd: number }) => {
			try {
				db.insert(aiUsage)
					.values({
						kind: 'translate',
						model: u.model,
						promptTokens: u.promptTokens,
						cachedTokens: u.cachedTokens,
						completionTokens: u.completionTokens,
						costUsd: u.costUsd,
					})
					.run();
			} catch {
				// NEVER LET LEDGER FAILURES TAKE DOWN THE JOB
			}
		},
	};

	const handle = startChapterJob(chapterId, chapterWork(chapterId, deps), { force });

	const stream = new ReadableStream<Uint8Array>({
		start(controller) {
			const encoder = new TextEncoder();
			let closed = false;
			// DECLARED BEFORE close() — THE SYNC REPLAY INSIDE subscribe() CAN FIRE close() IMMEDIATELY
			let unsubscribe: () => void = () => {};
			const close = () => {
				if (closed) return;
				closed = true;
				unsubscribe();
				try {
					controller.close();
				} catch {
					// ALREADY CLOSED BY THE CLIENT (cancel) — FINE
				}
			};
			unsubscribe = handle.subscribe((e) => {
				controller.enqueue(encoder.encode(`data: ${JSON.stringify(e)}\n\n`));
				if (e.type === 'done' || e.type === 'error') close();
			});
		},
		cancel() {
			// CLIENT DISCONNECTED — THE DETACHED JOB KEEPS RUNNING (BUFFERED EVENTS FOR THE NEXT READER)
		},
	});

	return new Response(stream, {
		headers: {
			'content-type': 'text/event-stream',
			'cache-control': 'no-cache',
			'x-accel-buffering': 'no',
		},
	});
};
