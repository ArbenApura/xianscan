// CHAPTER PIPELINE RUNNER TESTS — THE FULL PER-PAGE LOOP WITH FAKE SIDECAR + FAKE LLM + IN-MEMORY
// SQLITE + A TEMP DATA ROOT. NO NETWORK, NO MODELS, NO API KEY.
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createCanvas } from '@napi-rs/canvas';
import type OpenAI from 'openai';
import { eq } from 'drizzle-orm';
import { getTestDb, resetDb, seedBook, seedChapter, seedPage, type TestDb } from '../helpers/db';
import type { AnalyzeResult, PipelineClient } from '$lib/server/pipeline-client';
import { chapterWork } from '$lib/server/chapter-pipeline';
import { pages, regions } from '$lib/server/db/schema';

vi.mock('$lib/server/db', async () => ({ db: (await import('../helpers/db')).getTestDb() }));

// -- FAKES -- //

const PAGE_PNG = (() => {
	const c = createCanvas(200, 300);
	const x = c.getContext('2d');
	x.fillStyle = 'white';
	x.fillRect(0, 0, 200, 300);
	return c.toBuffer('image/png');
})();

class FakePipeline implements PipelineClient {
	preprocessCalls = 0;
	analyzeCalls = 0;
	cleanCalls = 0;
	failAnalyzeOn = new Set<number>(); // PAGE FILE PATHS THAT SHOULD FAIL ANALYZE

	async preprocess(image: Buffer, _signal?: AbortSignal): Promise<Buffer> {
		this.preprocessCalls++;
		return image;
	}

	async analyze(_image: Buffer, _signal?: AbortSignal): Promise<AnalyzeResult> {
		this.analyzeCalls++;
		return {
			width: 200,
			height: 300,
			backend: 'comic-ctd',
			regions: [
				{
					id: 'r0',
					box: { x: 20, y: 30, w: 100, h: 40 },
					polygon: [
						[20, 30],
						[120, 30],
						[120, 70],
						[20, 70],
					],
					category: 'dialogue',
					text: '你好',
					confidence: 0.95,
					vertical: false,
				},
			],
		};
	}

	async clean(image: Buffer, _regions: unknown[], _signal?: AbortSignal): Promise<Buffer> {
		this.cleanCalls++;
		return image;
	}

	async health() {
		return { status: 'ok', detector: 'comic-ctd', inpainter: 'opencv' };
	}
}

function fakeLlm(translations: Record<string, string> = { r0: 'Hello' }) {
	const client = {
		chat: {
			completions: {
				create: async () => ({
					choices: [{ message: { content: JSON.stringify(translations) } }],
					usage: { prompt_tokens: 50, completion_tokens: 10, total_tokens: 60 },
				}),
			},
		},
	} as unknown as OpenAI;
	return client;
}

// -- STATES -- //

let db: TestDb;
let dataRoot: string;
let pipeline: FakePipeline;

// -- LIFECYCLES -- //

beforeEach(() => {
	db = getTestDb();
	resetDb();
	dataRoot = mkdtempSync(join(tmpdir(), 'mt-pipeline-'));
	pipeline = new FakePipeline();
});

afterEach(() => {
	rmSync(dataRoot, { recursive: true, force: true });
});

// -- HELPERS -- //

function seedChapterWithPage(fileName: string) {
	seedBook(db, { id: 'b1' });
	const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
	const page = seedPage(db, { chapterId: chapter.id, seq: 0, filePath: `uploads/${fileName}` });
	mkdirSync(join(dataRoot, 'uploads'), { recursive: true });
	writeFileSync(join(dataRoot, 'uploads', fileName), PAGE_PNG);
	return { chapter, page };
}

async function run(chapterId: number, llm: OpenAI) {
	const events: string[] = [];
	await chapterWork(chapterId, { pipeline, dataRoot, llm })(new AbortController().signal, (e) =>
		events.push(e.type),
	);
	return events;
}

// -- TESTS -- //

describe('runChapterPipeline', () => {
	it('analyzes, translates, cleans, typesets and marks the page done', async () => {
		const { chapter, page } = seedChapterWithPage('c1-p0.png');
		await run(chapter.id, fakeLlm());

		const got = db.select().from(pages).where(eq(pages.id, page.id)).get();
		expect(got?.status).toBe('done');
		expect(got?.cleanedPath).toBe(`clean/${chapter.id}/0.png`);
		expect(got?.outputPath).toBe(`output/${chapter.id}/0.png`);
		expect(got?.width).toBe(200);

		// ARTIFACTS EXIST ON DISK
		expect(readFileSync(join(dataRoot, got!.cleanedPath!)).length).toBeGreaterThan(0);
		expect(readFileSync(join(dataRoot, got!.outputPath!)).length).toBeGreaterThan(0);

		// THE REGION ROW HAS OCR TEXT + TRANSLATION
		const region = db.select().from(regions).where(eq(regions.pageId, page.id)).get();
		expect(region?.textSource).toBe('你好');
		expect(region?.textTarget).toBe('Hello');
		expect(region?.status).toBe('translated');
		expect(JSON.parse(region!.polygon!)).toHaveLength(4);

		expect(pipeline.analyzeCalls).toBe(1);
		expect(pipeline.cleanCalls).toBe(1);
	});

	it('serves the second run from the translation cache (no second LLM call)', async () => {
		const { chapter, page } = seedChapterWithPage('c1-p0.png');
		const llm = fakeLlm();
		await run(chapter.id, llm);
		// SEND THE PAGE BACK TO 'pending' (e.g. CLEAR PROGRESS) SO THE SECOND RUN RE-ENTERS THE
		// PIPELINE INSTEAD OF SKIPPING THE 'done' PAGE — THE translations CACHE IS THE HIT PATH.
		db.update(pages).set({ status: 'pending' }).where(eq(pages.id, page.id)).run();
		await run(chapter.id, llm);

		const regions2 = db.select().from(regions).all();
		expect(regions2).toHaveLength(1); // REGIONS WERE REPLACED, NOT DUPLICATED
		expect(regions2[0].textTarget).toBe('Hello');
		expect(pipeline.analyzeCalls).toBe(2); // BOTH RUNS ANALYZED (NO SKIP) — CACHE SAVED THE LLM CALL
	});

	it('skips already-translated pages on re-run (resume without redundant work)', async () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		const p0 = seedPage(db, { chapterId: chapter.id, seq: 0, filePath: 'uploads/done.png' });
		const p1 = seedPage(db, { chapterId: chapter.id, seq: 1, filePath: 'uploads/new.png' });
		mkdirSync(join(dataRoot, 'uploads'), { recursive: true });
		writeFileSync(join(dataRoot, 'uploads', 'done.png'), PAGE_PNG);
		writeFileSync(join(dataRoot, 'uploads', 'new.png'), PAGE_PNG);

		await chapterWork(chapter.id, { pipeline, dataRoot, llm: fakeLlm() })(new AbortController().signal, () => {});
		expect(db.select().from(pages).where(eq(pages.id, p0.id)).get()?.status).toBe('done');

		// PAGE 1 GOES BACK TO 'pending' (e.g. CLEARED) — PAGE 0 STAYS 'done'
		db.update(pages).set({ status: 'pending' }).where(eq(pages.id, p1.id)).run();
		const callsBefore = pipeline.analyzeCalls;

		const events: string[] = [];
		await chapterWork(chapter.id, { pipeline, dataRoot, llm: fakeLlm() })(new AbortController().signal, (e) =>
			events.push(e.type),
		);

		// ONLY PAGE 1 WAS RE-ANALYZED — PAGE 0 WAS SKIPPED AND KEPT ITS OUTPUT
		expect(pipeline.analyzeCalls - callsBefore).toBe(1);
		// BOTH PAGES REPORT DONE (THE SKIPPED PAGE EMITS ITS page-done UP FRONT, IN ORDER)
		expect(events).toEqual(['page-done', 'page-done']);
		const got0 = db.select().from(pages).where(eq(pages.id, p0.id)).get();
		const got1 = db.select().from(pages).where(eq(pages.id, p1.id)).get();
		expect(got0?.status).toBe('done');
		expect(got0?.outputPath).toBe(`output/${chapter.id}/0.png`); // UNTOUCHED
		expect(got1?.status).toBe('done');
	});

	it('isolates per-page failures: one bad page, the rest finish', async () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		const _good = seedPage(db, { chapterId: chapter.id, seq: 0, filePath: 'uploads/good.png' });
		const _bad = seedPage(db, { chapterId: chapter.id, seq: 1, filePath: 'uploads/bad.png' });
		mkdirSync(join(dataRoot, 'uploads'), { recursive: true });
		writeFileSync(join(dataRoot, 'uploads', 'good.png'), PAGE_PNG);
		// THE BAD PAGE MUST DIFFER BYTE-WISE SO THE FAILURE INJECTION CAN DISTINGUISH THEM
		const badPng = (() => {
			const c = createCanvas(200, 300);
			const x = c.getContext('2d');
			x.fillStyle = 'gray';
			x.fillRect(0, 0, 200, 300);
			return c.toBuffer('image/png');
		})();
		writeFileSync(join(dataRoot, 'uploads', 'bad.png'), badPng);

		const failing = new FakePipeline();
		const badBytes = readFileSync(join(dataRoot, 'uploads', 'bad.png'));
		const originalAnalyze = failing.analyze.bind(failing);
		failing.analyze = async (image, signal) => {
			if (image.equals(badBytes)) {
				throw new Error('sidecar exploded');
			}
			return originalAnalyze(image, signal);
		};

		const events: string[] = [];
		await chapterWork(chapter.id, { pipeline: failing, dataRoot, llm: fakeLlm() })(
			new AbortController().signal,
			(e) => events.push(e.type),
		);

		const pages2 = db
			.select()
			.from(pages)
			.where(eq(pages.chapterId, chapter.id))
			.orderBy(pages.seq)
			.all();
		expect(pages2[0].status).toBe('done');
		expect(pages2[1].status).toBe('error');
		expect(pages2[1].error).toContain('sidecar exploded');
		expect(events.filter((t) => t === 'error').length).toBe(1);
		expect(events.filter((t) => t === 'page-done').length).toBe(1);
	});

	it('aborts between pages when the signal fires', async () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		const p1 = seedPage(db, { chapterId: chapter.id, seq: 0, filePath: 'uploads/p1.png' });
		const p2 = seedPage(db, { chapterId: chapter.id, seq: 1, filePath: 'uploads/p2.png' });
		mkdirSync(join(dataRoot, 'uploads'), { recursive: true });
		writeFileSync(join(dataRoot, 'uploads', 'p1.png'), PAGE_PNG);
		writeFileSync(join(dataRoot, 'uploads', 'p2.png'), PAGE_PNG);

		const controller = new AbortController();
		// ABORT DURING THE FIRST PAGE'S ANALYZE — THE JOB MUST STOP AT THE PHASE BOUNDARY
		const slowPipeline = new FakePipeline();
		const original = slowPipeline.analyze.bind(slowPipeline);
		slowPipeline.analyze = async (image, signal) => {
			const r = await original(image, signal);
			controller.abort();
			return r;
		};

		// AN ABORT STOPS THE JOB — THE WORK FUNCTION RETHROWS THE AbortError (SUPERSEDE TAKES OVER).
		// pageConcurrency: 1 KEEPS THE ORDERING DETERMINISTIC.
		await expect(
			chapterWork(chapter.id, { pipeline: slowPipeline, dataRoot, llm: fakeLlm(), pageConcurrency: 1 })(
				controller.signal,
				() => {},
			),
		).rejects.toMatchObject({ name: 'AbortError' });

		const p1row = db.select().from(pages).where(eq(pages.id, p1.id)).get();
		const skipped = db.select().from(pages).where(eq(pages.id, p2.id)).get();
		expect(p1row?.status).toBe('processing'); // PHASE 1 FINISHED; PHASE 3 NEVER STARTED
		expect(p1row?.status).not.toBe('error'); // AN ABORT NEVER MARKS PAGES AS ERRORS
		expect(skipped?.status).toBe('pending'); // NEVER STARTED
	});

	it('resets pages stuck in processing (crash resume) before running', async () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		const page = seedPage(db, { chapterId: chapter.id, seq: 0, filePath: 'uploads/stuck.png' });
		mkdirSync(join(dataRoot, 'uploads'), { recursive: true });
		writeFileSync(join(dataRoot, 'uploads', 'stuck.png'), PAGE_PNG);
		// SIMULATE A CRASH MID-JOB: THE PAGE IS STUCK IN 'processing'
		db.update(pages).set({ status: 'processing' }).where(eq(pages.id, page.id)).run();

		await run(chapter.id, fakeLlm());

		const got = db.select().from(pages).where(eq(pages.id, page.id)).get();
		expect(got?.status).toBe('done'); // THE RESET LET THE RE-RUN COMPLETE IT
	});

	it('translations update only their own region (seq keyed correctly)', async () => {
		const { chapter, page } = seedChapterWithPage('c1-p0.png');
		// A TWO-REGION PAGE
		const multi = new FakePipeline();
		multi.analyze = async () => ({
			width: 200,
			height: 300,
			backend: 'comic-ctd',
			regions: [
				{ id: 'r0', box: { x: 0, y: 0, w: 50, h: 20 }, polygon: [[0, 0]], category: 'dialogue', text: '甲', confidence: 0.9, vertical: false },
				{ id: 'r1', box: { x: 0, y: 100, w: 50, h: 20 }, polygon: [[0, 100]], category: 'sfx', text: '轰', confidence: 0.9, vertical: false },
			],
		});
		await chapterWork(chapter.id, { pipeline: multi, dataRoot, llm: fakeLlm({ r0: 'A', r1: 'BOOM' }) })(
			new AbortController().signal,
			() => {},
		);

		const rows = db.select().from(regions).where(eq(regions.pageId, page.id)).orderBy(regions.seq).all();
		expect(rows).toHaveLength(2);
		expect(rows[0].textTarget).toBe('A');
		expect(rows[1].textTarget).toBe('BOOM');
	});

	it('ignores watermarks and preserves them untouched without inpainting or translation', async () => {
		const { chapter, page } = seedChapterWithPage('c1-p0.png');
		let cleanedRegionsPassed: unknown[] = [];
		const wmPipeline = new FakePipeline();
		wmPipeline.analyze = async () => ({
			width: 200,
			height: 300,
			backend: 'comic-ctd',
			regions: [
				{ id: 'r0', box: { x: 10, y: 10, w: 50, h: 20 }, polygon: [[10, 10]], category: 'dialogue', text: '你好', confidence: 0.9, vertical: false },
				{ id: 'r1', box: { x: 100, y: 10, w: 90, h: 20 }, polygon: [[100, 10]], category: 'other', text: 'www.baozimh.com', confidence: 0.9, vertical: false },
			],
		});
		wmPipeline.clean = async (_image: Buffer, regionsPassed: unknown[]) => {
			cleanedRegionsPassed = regionsPassed;
			return PAGE_PNG;
		};

		const llmReceivedSources: string[] = [];
		const customLlm = {
			chat: {
				completions: {
					create: async (params: { messages: { content: string }[] }) => {
						llmReceivedSources.push(params.messages[1]?.content || '');
						return {
							choices: [{ message: { content: JSON.stringify({ r0: 'Hello', r1: '' }) } }],
							usage: { prompt_tokens: 20, completion_tokens: 5, total_tokens: 25 },
						};
					},
				},
			},
		} as unknown as OpenAI;

		await chapterWork(chapter.id, { pipeline: wmPipeline, dataRoot, llm: customLlm })(
			new AbortController().signal,
			() => {},
		);

		// CLEAN ONLY RECEIVES REGIONS WITH VALID TRANSLATIONS (1 TOTAL — WATERMARK r1 IS LEFT UNTOUCHED)
		expect(cleanedRegionsPassed).toHaveLength(1);

		const rows = db.select().from(regions).where(eq(regions.pageId, page.id)).orderBy(regions.seq).all();
		expect(rows).toHaveLength(2);
		expect(rows[0].textTarget).toBe('Hello');
		expect(rows[1].textTarget).toBeNull(); // WATERMARK HAS NO TRANSLATED TARGET
	});

	it('does not re-record spend on translation cache hits', async () => {
		const { chapter, page } = seedChapterWithPage('c1-p0.png');
		const llm = fakeLlm();
		const usages: unknown[] = [];
		const deps = { pipeline, dataRoot, llm, onUsage: (u: unknown) => usages.push(u) };
		await chapterWork(chapter.id, deps)(new AbortController().signal, () => {});
		// SEND THE PAGE BACK TO 'pending' SO THE SECOND RUN TAKES THE CACHE-HIT PATH (NOT THE SKIP)
		db.update(pages).set({ status: 'pending' }).where(eq(pages.id, page.id)).run();
		await chapterWork(chapter.id, deps)(new AbortController().signal, () => {});
		// RUN 1: CHAPTER EXTRACTION (1) + TRANSLATION (1). RUN 2: EXTRACTION (1) + CACHE HIT (0).
		expect(usages.length).toBe(3);
	});

	it('skips pages entirely on re-run when everything is done (no extraction call either)', async () => {
		const { chapter } = seedChapterWithPage('c1-p0.png');
		const llm = fakeLlm();
		const usages: unknown[] = [];
		const deps = { pipeline, dataRoot, llm, onUsage: (u: unknown) => usages.push(u) };
		await chapterWork(chapter.id, deps)(new AbortController().signal, () => {});
		await chapterWork(chapter.id, deps)(new AbortController().signal, () => {});
		// RUN 1: EXTRACTION + TRANSLATION = 2. RUN 2: EVERYTHING SKIPPED — NO LLM CALLS AT ALL.
		expect(usages.length).toBe(2);
	});

	it('processes pages concurrently within each phase', async () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		mkdirSync(join(dataRoot, 'uploads'), { recursive: true });
		for (let i = 0; i < 3; i++) {
			seedPage(db, { chapterId: chapter.id, seq: i, filePath: `uploads/c${i}.png` });
			writeFileSync(join(dataRoot, `uploads/c${i}.png`), PAGE_PNG);
		}

		// TRACK CONCURRENT ANALYZE CALLS — PARALLEL PHASE 1 MUST OVERLAP THEM
		let inFlight = 0;
		let maxInFlight = 0;
		const concurrent = new FakePipeline();
		const original = concurrent.analyze.bind(concurrent);
		concurrent.analyze = async (image, signal) => {
			inFlight++;
			maxInFlight = Math.max(maxInFlight, inFlight);
			await new Promise((r) => setTimeout(r, 10));
			const result = await original(image, signal);
			inFlight--;
			return result;
		};

		const events: string[] = [];
		await chapterWork(chapter.id, {
			pipeline: concurrent,
			dataRoot,
			llm: fakeLlm(),
			pageConcurrency: 3,
		})(new AbortController().signal, (e) => events.push(e.type));

		expect(maxInFlight).toBeGreaterThan(1); // ANALYZE CALLS OVERLAPPED
		const rows = db
			.select()
			.from(pages)
			.where(eq(pages.chapterId, chapter.id))
			.orderBy(pages.seq)
			.all();
		expect(rows.every((r) => r.status === 'done')).toBe(true);
		// EVENTS ARRIVE IN PAGE ORDER EVEN THOUGH PAGES FINISH OUT OF ORDER
		expect(events).toEqual(['page-done', 'page-done', 'page-done']);
	});
});

