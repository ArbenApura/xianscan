// CHAPTER PIPELINE RUNNER — ORCHESTRATES ONE CHAPTER'S PAGES THROUGH THE FULL PIPELINE:
//
//   analyze (sidecar detect+OCR) → persist regions → translate (DeepSeek + glossary + cache)
//   → clean (sidecar inpaint) → typeset (TS/Skia) → persist outputs
//
// STAGED PARALLEL EXECUTION (THE SIDECAR CPU WORK AND THE LLM ARE THE TWO LONG POLES):
//   PHASE 1 — analyze ALL pages concurrently (sidecar threadpool) + persist regions.
//   PHASE 2 — ONE chapter-level LLM call extracts glossary terms (was one call PER PAGE).
//   PHASE 3 — translate + clean + typeset concurrently across pages (LLM calls share the global
//             PQueue; sidecar clean calls parallelize on its threadpool).
//   EVENTS — emitted in page order via a completion watermark (pages finish out of order).
//
// CONTRACT:
//   - PER-PAGE ERROR ISOLATION: ONE BAD PAGE MARKS ITSELF 'error' AND THE JOB CONTINUES.
//   - THE WORK FUNCTION FITS startChapterJob() (translation-service) — signal + emit.
//   - ALL FILE PATHS ARE RELATIVE TO dataRoot (web/data/); THE API LAYER PASSES IT.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type OpenAI from 'openai';
// IMPORTED ENVS ($env/...)
import { env } from '$env/dynamic/private';
// IMPORTED DEP-MODULES
import PQueue from 'p-queue';
import { and, asc, eq } from 'drizzle-orm';
// IMPORTED TYPES
import type { TranslationUsage } from '$lib/types';
// IMPORTED MODULES
import { addNewTerms, bookPair, getEffectiveGlossary } from './glossary';
import { matchTerms } from './glossary-match';
import { getCachedPageTranslation, pageCacheKey, savePageTranslation } from './cache';
import type { JobEvent } from './translation-service';
import type { AnalyzeResult, PipelineClient, PipelineRegion } from './pipeline-client';
import { db } from './db';
import { chapters, pages, regions, type Page } from './db/schema';
import { extractTerms, translatePage } from './translate';
import { typesetPage } from './typeset';

// -- TYPES -- //

export interface ChapterPipelineDeps {
	pipeline: PipelineClient;
	/** INJECTABLE LLM — TESTS PASS A FAKE; PRODUCTION USES THE DEEPSEEK SINGLETON. */
	llm?: OpenAI;
	model?: string;
	/**
	 * OPACQUE PROVIDER DISCRIMINATOR FOR THE TRANSLATION CACHE — THE API LAYER SETS IT FROM
	 * DEEPSEEK_BASE_URL SO SWITCHING PROVIDERS (e.g. MOCK ↔ REAL) NEVER SERVES STALE CACHED TEXT.
	 */
	cacheSalt?: string;
	/** ABSOLUTE PATH TO THE APP DATA ROOT (web/data/) — ALL RELATIVE PATHS RESOLVE AGAINST IT. */
	dataRoot: string;
	onUsage?: (usage: TranslationUsage) => void;
	/** MAX CONCURRENT PAGES PER PHASE (TESTS PIN 1 FOR DETERMINISM). */
	pageConcurrency?: number;
}

export type PipelineEmit = (e: JobEvent) => void;

// -- CONSTANTS -- //

// HOW MANY PAGES THE CHAPTER PIPELINE PROCESSES CONCURRENTLY. THE SIDECAR SERVES EACH REQUEST ON A
// THREADPOOL THREAD (CPU-HEAVY DETECT+OCR), SO 3-4 OVERLAPPING PAGES SATURATE A TYPICAL QUAD-CORE
// WITHOUT THRASHING. TUNE VIA PIPELINE_PAGE_CONCURRENCY (e.g. 6 ON AN 8-CORE BOX).
const PAGE_CONCURRENCY = Math.max(1, Number(env.PIPELINE_PAGE_CONCURRENCY ?? '3') || 3);

// CHARACTER CAP FOR THE CHAPTER-LEVEL TERM-EXTRACTION PROMPT — EXTRACTION IS TOKEN-BOUND, SO A VERY
// LONG CHAPTER IS TRUNCATED (THE FIRST PAGES ARE REPRESENTATIVE).
const MAX_EXTRACT_CHARS = 20_000;

// -- INTERNALS -- //

function regionRow(region: PipelineRegion, seq: number) {
	return {
		seq,
		box: JSON.stringify(region.box),
		polygon: JSON.stringify(region.polygon),
		category: region.category,
		textSource: region.text,
		conf: region.confidence,
		status: 'pending' as const,
	};
}

function cleanDir(path: string): void {
	mkdirSync(path, { recursive: true });
}

// PER-PAGE SLOT — CARRIES THE PAGE THROUGH THE STAGES; `outcome` DOUBLES AS THE ORDERED-EVENT
type PageSlot = {
	page: Page;
	image?: Buffer;
	analyzed?: AnalyzeResult;
	outcome?: 'analyzed' | 'done' | 'error';
	message?: string;
};

// -- THE WORK FUNCTION (FITS startChapterJob) -- //

export async function runChapterPipeline(
	chapterId: number,
	deps: ChapterPipelineDeps,
	signal: AbortSignal,
	emit: PipelineEmit,
): Promise<void> {
	const chapter = db.select().from(chapters).where(eq(chapters.id, chapterId)).get();
	if (!chapter) throw new Error(`chapter ${chapterId} not found`);

	// CRASH-RESUME: A BACKEND RESTART CAN LEAVE PAGES STUCK IN 'processing' — RESET THEM SO A RE-RUN
	// CAN PICK THEM UP (ONLY 'done' PAGES COUNT AS FINISHED).
	db.update(pages)
		.set({ status: 'pending', error: null })
		.where(and(eq(pages.chapterId, chapterId), eq(pages.status, 'processing')))
		.run();

	const pageRows = db
		.select()
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(asc(pages.seq))
		.all();

	if (pageRows.length === 0) {
		emit({ type: 'done', chapterId });
		return;
	}

	const pool = new PQueue({ concurrency: deps.pageConcurrency ?? PAGE_CONCURRENCY });
	const pair: LangPair = { sourceLang: chapter.sourceLang, targetLang: chapter.targetLang };
	const model = deps.model;

	// -- EMISSION WATERMARK STATE -- //
	const slots: PageSlot[] = pageRows.map((page) => ({
		page,
		outcome: page.status === 'done' ? 'done' : undefined,
	}));
	let nextEmitIdx = 0;

	// FLUSH EVENTS IN STRICT ASCENDING PAGE ORDER (PAGE 0 BEFORE PAGE 1, ALWAYS).
	function flushEvents(): void {
		while (nextEmitIdx < slots.length) {
			const slot = slots[nextEmitIdx];
			if (slot.outcome === undefined || slot.outcome === 'analyzed') {
				break; // BLOCKED — WAIT FOR THIS PAGE TO REACH A TERMINAL STATE ('done' | 'error')
			}
			if (slot.outcome === 'done') {
				emit({
					type: 'page-done',
					chapterId,
					page: nextEmitIdx,
					pageCount: slots.length,
					outputPath: slot.page.outputPath,
				});
			} else if (slot.outcome === 'error') {
				emit({
					type: 'error',
					chapterId,
					page: nextEmitIdx,
					message: slot.message ?? 'Unknown error',
				});
			}
			nextEmitIdx++;
		}
	}


	// -- PHASE 1 (PARALLEL): READ IMAGE + DETECT + OCR -- //
	await pool.addAll(
		pageRows.map((page, i) => async () => {
			const slot = slots[i];
			if (slot.outcome !== undefined) return; // SKIPPED — ALREADY 'done' UP FRONT
			try {
				signal.throwIfAborted();
				// 'processing' MAKES THE CRASH-RESUME RESET ABOVE REAL (A CRASH NOW LEAVES A MARKER).
				db.update(pages)
					.set({ status: 'processing', error: null })
					.where(eq(pages.id, page.id))
					.run();

				// 1) ANALYZE — DETECT + OCR VIA THE SIDECAR
				const image = readFileSync(join(deps.dataRoot, page.filePath));
				const analyzed = await deps.pipeline.analyze(image, signal);

				// 2) PERSIST REGIONS (REPLACE THE PREVIOUS RUN'S)
				db.delete(regions).where(eq(regions.pageId, page.id)).run();
				if (analyzed.regions.length > 0) {
					db.insert(regions)
						.values(analyzed.regions.map((r, idx) => ({ ...regionRow(r, idx), pageId: page.id })))
						.run();
				}

				slot.image = image;
				slot.analyzed = analyzed;
				slot.outcome = 'analyzed';
			} catch (e) {
				// AN ABORT STOPS THE WHOLE JOB — THE NEXT SUPERSEDING RUN TAKES OVER. NEVER MARK THE PAGE.
				if (signal.aborted) throw e;
				// PER-PAGE ERROR ISOLATION — THE JOB CONTINUES WITH THE OTHER PAGES
				const message = e instanceof Error ? e.message : String(e);
				db.update(pages).set({ status: 'error', error: message }).where(eq(pages.id, page.id)).run();
				slot.outcome = 'error';
				slot.message = message;
				flushEvents();
			}
		}),
	);

	// EMIT UP FRONT: SKIPPED ('done') PAGES AND PHASE-1 FAILURES REACH THE UI IN PAGE ORDER NOW,
	// SO THE PROGRESS BAR REFLECTS THEM WHILE THE REST STILL RUNS.
	flushEvents();

	// -- PHASE 2: ONE CHAPTER-LEVEL TERM-EXTRACTION CALL (NON-BLOCKING) -- //
	const chapterText = slots
		.filter((s) => s.outcome === 'analyzed')
		.flatMap((s) => (s.analyzed?.regions ?? []).filter((r) => r.text.trim().length > 0).map((r) => r.text))
		.join('\n')
		.slice(0, MAX_EXTRACT_CHARS);
	if (chapterText.trim().length > 0) {
		try {
			const { terms: extracted, usage: extUsage } = await extractTerms(chapterText, pair, {
				client: deps.llm,
				model,
				signal,
			});
			if (extracted.length > 0) {
				await addNewTerms(chapter.bookId, extracted, chapterId);
			}
			if (extUsage && deps.onUsage) deps.onUsage(extUsage);
		} catch {
			// AUTO-EXTRACTION IS NON-BLOCKING FOR TRANSLATION
		}
	}

	// -- PHASE 3 (PARALLEL): TRANSLATE → CLEAN → TYPESET → MARK DONE -- //
	await pool.addAll(
		pageRows.map((page, i) => async () => {
			const slot = slots[i];
			if (slot.outcome !== 'analyzed') return; // PHASE-1 FAILURES SKIP THE REST
			const image = slot.image!;
			const analyzed = slot.analyzed!;
			try {
				signal.throwIfAborted();

				// 3) TRANSLATE — LLM SEMANTICALLY TRANSLATES VALID DIALOGUE & EMITS "" FOR WATERMARKS/STAMPS
				const sources = analyzed.regions
					.filter((r) => r.text.trim().length > 0)
					.map((r) => ({ id: r.id, text: r.text, category: r.category }));
				const byRegion = new Map<string, string>();
				if (sources.length > 0) {
					const pageText = sources.map((s) => s.text).join('\n');

					// 3b) AHO-CORASICK TERM MATCHING — FILTER TO TERMS PRESENT ON THIS PAGE (+ PINNED)
					const matched = await matchTerms(chapter.bookId, pageText);
					const matchedSources = new Set(matched.map((m) => m.source));
					const currentEffective = await getEffectiveGlossary(chapter.bookId);
					const pageTerms = currentEffective.filter((t) => t.pinned || matchedSources.has(t.source));

					const cacheKey = pageCacheKey(sources, pageTerms, model ?? 'default', pair, deps.cacheSalt);
					const cached = getCachedPageTranslation(page.id, cacheKey);
					if (cached) {
						for (const [id, text] of cached.byRegion) byRegion.set(id, text);
						// CACHE HIT → NOTHING SPENT ON THE LLM: DO NOT RE-RECORD THE CACHED USAGE ROW.
					} else {
						const translated = await translatePage(sources, pageTerms, pair, {
							client: deps.llm,
							model,
							signal,
						});
						for (const [id, text] of translated.byRegion) byRegion.set(id, text);
						savePageTranslation(page.id, cacheKey, byRegion, translated.usage.model, translated.usage);
						if (translated.usage && deps.onUsage) deps.onUsage(translated.usage);
					}
				}

				// 4) WRITE THE TRANSLATIONS BACK TO THE REGION ROWS
				const seqById = new Map(analyzed.regions.map((r, idx) => [r.id, idx]));
				for (const region of analyzed.regions) {
					const target = byRegion.get(region.id) ?? '';
					db.update(regions)
						.set({ textTarget: target || null, status: target ? 'translated' : 'failed' })
						.where(and(eq(regions.pageId, page.id), eq(regions.seq, seqById.get(region.id) ?? -1)))
						.run();
				}

				// 5) CLEAN — UNIVERSALLY ERASE ALL DETECTED TEXT & WATERMARK REGIONS WITH LAMA INPAINTING
				const cleanRegions = analyzed.regions.map((r) => ({ id: r.id, box: r.box, polygon: r.polygon }));
				const cleaned = await deps.pipeline.clean(image, cleanRegions, signal);
				const cleanPath = `clean/${chapterId}/${page.seq}.png`;
				const cleanAbs = join(deps.dataRoot, cleanPath);
				cleanDir(join(deps.dataRoot, 'clean', String(chapterId)));
				writeFileSync(cleanAbs, cleaned);

				// 6) TYPESET — RENDER TRANSLATIONS ONLY FOR REGIONS WITH NON-EMPTY DIALOGUE
				const typesetRegions = analyzed.regions
					.filter((r) => Boolean(byRegion.get(r.id)?.trim()))
					.map((r) => ({
						id: r.id,
						box: r.box,
						text: byRegion.get(r.id)!,
						category: r.category,
						vertical: r.vertical,
						angle: r.angle,
					}));
				const out = await typesetPage(cleaned, typesetRegions);
				const outputPath = `output/${chapterId}/${page.seq}.png`;
				cleanDir(join(deps.dataRoot, 'output', String(chapterId)));
				writeFileSync(join(deps.dataRoot, outputPath), out);

				// 7) MARK DONE
				db.update(pages)
					.set({
						status: 'done',
						cleanedPath: cleanPath,
						outputPath,
						width: analyzed.width,
						height: analyzed.height,
					})
					.where(eq(pages.id, page.id))
					.run();
				slot.outcome = 'done';
				flushEvents();
			} catch (e) {
				// AN ABORT STOPS THE WHOLE JOB — THE NEXT SUPERSEDING RUN TAKES OVER. NEVER MARK THE PAGE.
				if (signal.aborted) throw e;
				// PER-PAGE ERROR ISOLATION — THE JOB CONTINUES WITH THE OTHER PAGES
				const message = e instanceof Error ? e.message : String(e);
				db.update(pages).set({ status: 'error', error: message }).where(eq(pages.id, page.id)).run();
				slot.outcome = 'error';
				slot.message = message;
				flushEvents();
			}
		}),
	);

	flushEvents();
}

// -- HELPERS FOR THE API LAYER -- //

/** BUILD THE WORK FUNCTION A JOB RUNS — BINDS THE RUNNER TO startChapterJob's SIGNATURE. */
export function chapterWork(chapterId: number, deps: ChapterPipelineDeps) {
	return (signal: AbortSignal, emit: PipelineEmit) => runChapterPipeline(chapterId, deps, signal, emit);
}
