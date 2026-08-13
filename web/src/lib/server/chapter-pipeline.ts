// CHAPTER PIPELINE RUNNER — ORCHESTRATES ONE CHAPTER'S PAGES THROUGH THE FULL PIPELINE:
//
//   analyze (sidecar detect+OCR) → persist regions → translate (DeepSeek + glossary + cache)
//   → clean (sidecar inpaint) → typeset (TS/Skia) → persist outputs
//
// CONTRACT:
//   - PER-PAGE ERROR ISOLATION: ONE BAD PAGE MARKS ITSELF 'error' AND THE JOB CONTINUES.
//   - THE WORK FUNCTION FITS startChapterJob() (translation-service) — signal + emit.
//   - ALL FILE PATHS ARE RELATIVE TO dataRoot (web/data/); THE API LAYER PASSES IT.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type OpenAI from 'openai';
// IMPORTED DEP-MODULES
import { and, eq } from 'drizzle-orm';
// IMPORTED TYPES
import type { TranslationUsage } from '$lib/types';
// IMPORTED MODULES
import { addNewTerms, bookPair, getEffectiveGlossary } from './glossary';
import { matchTerms } from './glossary-match';
import { getCachedPageTranslation, pageCacheKey, savePageTranslation } from './cache';

import type { JobEvent } from './translation-service';
import type { PipelineClient, PipelineRegion } from './pipeline-client';
import { db } from './db';
import { chapters, pages, regions } from './db/schema';
import { extractTerms, translatePage } from './translate';
import { typesetPage } from './typeset';


import { filterWatermarkRegions } from './watermark';

// -- TYPES -- //

export interface ChapterPipelineDeps {
	pipeline: PipelineClient;
	/** INJECTABLE LLM — TESTS PASS A FAKE; PRODUCTION USES THE DEEPSEEK SINGLETON. */
	llm?: OpenAI;
	model?: string;
	/** OPTIONAL FLAG TO ENABLE STEP 0 PRE-PROCESSING & WATERMARK FILTERING. */
	enableWatermarkRemoval?: boolean;
	/** CUSTOM WATERMARK PATTERNS TO DETECT & ERASE WITHOUT TRANSLATING. */
	customWatermarks?: string[];
	/**
	 * OPACQUE PROVIDER DISCRIMINATOR FOR THE TRANSLATION CACHE — THE API LAYER SETS IT FROM
	 * DEEPSEEK_BASE_URL SO SWITCHING PROVIDERS (e.g. MOCK ↔ REAL) NEVER SERVES STALE CACHED TEXT.
	 */
	cacheSalt?: string;
	/** ABSOLUTE PATH TO THE APP DATA ROOT (web/data/) — ALL RELATIVE PATHS RESOLVE AGAINST IT. */
	dataRoot: string;
	onUsage?: (usage: TranslationUsage) => void;
}

export type PipelineEmit = (e: JobEvent) => void;

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
	// IS CLEAN (THE RUNNER IS IDEMPOTENT: REGIONS REPLACED, OUTPUTS OVERWRITTEN).
	db.update(pages)
		.set({ status: 'pending', error: null })
		.where(and(eq(pages.chapterId, chapterId), eq(pages.status, 'processing')))
		.run();

	const pair = await bookPair(chapter.bookId);
	const terms = await getEffectiveGlossary(chapter.bookId);

	const pageRows = db
		.select()
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(pages.seq)
		.all();


	const model = deps.model;

	for (let i = 0; i < pageRows.length; i++) {
		const page = pageRows[i];
		try {
			signal.throwIfAborted();
			// 0) STEP 0: PRE-PROCESS RAW IMAGE TO REMOVE WATERMARKS (IF ENABLED IN SETTINGS)
			const rawImage = readFileSync(join(deps.dataRoot, page.filePath));
			let image = rawImage;
			if (deps.enableWatermarkRemoval) {
				try {
					image = await deps.pipeline.preprocess(rawImage, signal);
				} catch {
					// FALLBACK TO RAW IMAGE IF SIDECAR PREPROCESS IS NOT UP YET
				}
			}

			// 1) ANALYZE — DETECT + OCR VIA THE SIDECAR
			const analyzed = await deps.pipeline.analyze(image, signal);

			// 1b) WATERMARK TEXT FILTER & SMART LINE-LEVEL SPLITTING
			const { textRegions } = filterWatermarkRegions(analyzed.regions, deps.customWatermarks);

			// 2) PERSIST REGIONS (REPLACE THE PREVIOUS RUN'S)
			db.delete(regions).where(eq(regions.pageId, page.id)).run();
			if (analyzed.regions.length > 0) {
				db.insert(regions)
					.values(analyzed.regions.map((r, idx) => ({ ...regionRow(r, idx), pageId: page.id })))
					.run();
			}

			// 3) TRANSLATE — EXCLUDE WATERMARKS FROM DEEPSEEK CALLS TO SAVE TOKENS
			const sources = textRegions
				.filter((r) => r.text.trim().length > 0)
				.map((r) => ({ id: r.id, text: r.text, category: r.category }));
			const byRegion = new Map<string, string>();
			if (sources.length > 0) {
				const pageText = sources.map((s) => s.text).join('\n');

				// 3a) AI AUTO-DETECTION & EXTRACTION OF NEW TERMS
				try {
					const { terms: extracted, usage: extUsage } = await extractTerms(pageText, pair, {
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

				// 3b) AHO-CORASICK TERM MATCHING — FILTER TO TERMS PRESENT ON THIS PAGE (+ PINNED)
				const matched = await matchTerms(chapter.bookId, pageText);
				const matchedSources = new Set(matched.map((m) => m.source));
				const currentEffective = await getEffectiveGlossary(chapter.bookId);
				const pageTerms = currentEffective.filter((t) => t.pinned || matchedSources.has(t.source));

				const cacheKey = pageCacheKey(sources, pageTerms, model ?? 'default', pair, deps.cacheSalt);
				const cached = getCachedPageTranslation(page.id, cacheKey);
				let usage: TranslationUsage | null = null;
				if (cached) {
					for (const [id, text] of cached.byRegion) byRegion.set(id, text);
					usage = cached.usage;
				} else {
					const translated = await translatePage(sources, pageTerms, pair, {
						client: deps.llm,
						model,
						signal,
					});
					for (const [id, text] of translated.byRegion) byRegion.set(id, text);
					usage = translated.usage;
					savePageTranslation(page.id, cacheKey, byRegion, usage.model, usage);
				}
				if (usage && deps.onUsage) deps.onUsage(usage);
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

			// 5) CLEAN — ERASE TEXT WITH LAMA INPAINTING
			// WHEN enableWatermarkRemoval IS TRUE: ERASE BOTH DIALOGUE TEXT AND WATERMARKS
			// WHEN enableWatermarkRemoval IS FALSE: ERASE ONLY DIALOGUE TEXT (LEAVE WATERMARKS/SITE STAMPS UNTOUCHED ON ORIGINAL ARTWORK)
			const targetCleanList = deps.enableWatermarkRemoval ? analyzed.regions : textRegions;
			const cleanRegions = targetCleanList.map((r) => ({ id: r.id, box: r.box, polygon: r.polygon }));
			const cleaned = await deps.pipeline.clean(image, cleanRegions, signal);
			const cleanPath = `clean/${chapterId}/${page.seq}.png`;
			const cleanAbs = join(deps.dataRoot, cleanPath);
			cleanDir(join(deps.dataRoot, 'clean', String(chapterId)));
			writeFileSync(cleanAbs, cleaned);

			// 6) TYPESET — RENDER TRANSLATIONS ONLY FOR DIALOGUE TEXT (EXCLUDING WATERMARKS)
			const out = await typesetPage(
				cleaned,
				textRegions.map((r) => ({
					id: r.id,
					box: r.box,
					text: byRegion.get(r.id) ?? '',
					category: r.category,
					vertical: r.vertical,
				})),
			);
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
			emit({ type: 'page-done', page: i, pageCount: pageRows.length });
		} catch (e) {
			// AN ABORT STOPS THE WHOLE JOB — THE NEXT SUPERSEDING RUN TAKES OVER. NEVER MARK THE PAGE.
			if (signal.aborted) throw e;
			// PER-PAGE ERROR ISOLATION — THE JOB CONTINUES WITH THE NEXT PAGE
			const message = e instanceof Error ? e.message : String(e);
			db.update(pages).set({ status: 'error', error: message }).where(eq(pages.id, page.id)).run();
			emit({ type: 'error', page: i, pageCount: pageRows.length, message });
		}
	}
}

// -- HELPERS FOR THE API LAYER -- //

/** BUILD THE WORK FUNCTION A JOB RUNS — BINDS THE RUNNER TO startChapterJob's SIGNATURE. */
export function chapterWork(chapterId: number, deps: ChapterPipelineDeps) {
	return (signal: AbortSignal, emit: PipelineEmit) => runChapterPipeline(chapterId, deps, signal, emit);
}
