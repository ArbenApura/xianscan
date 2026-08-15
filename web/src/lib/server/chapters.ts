// CHAPTER / PAGE CREATION HELPERS — SHARED BY THE API ROUTES.
import { randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { join, extname } from 'node:path';
// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { and, desc, eq, inArray } from 'drizzle-orm';

// IMPORTED MODULES
import { db } from './db';
import { books, chapters, pages, regions, translations } from './db/schema';
import { clearChapterJob } from './translation-service';
import { DATA_ROOT } from './paths';
import type { PipelineClient } from './pipeline-client';

// -- CONSTANTS -- //

// ACCEPTED PAGE IMAGE FORMATS (MAGIC-BYTE CHECKED IN uploadImages)
const ALLOWED_EXT = new Set(['.png', '.jpg', '.jpeg', '.webp', '.avif']);

// -- FUNCTIONS -- //

export async function assertChapterExists(chapterId: number): Promise<{ id: number; bookId: string; title: string; seq: number }> {
	const chapter = db.select().from(chapters).where(eq(chapters.id, chapterId)).get();
	if (!chapter) throw error(404, 'Chapter not found.');
	return chapter;
}

// CREATE AN EMPTY CHAPTER AT THE END OF THE BOOK.
export async function createChapter(bookId: string, title: string): Promise<{ id: number; seq: number }> {
	const max = db
		.select({ seq: chapters.seq })
		.from(chapters)
		.where(eq(chapters.bookId, bookId))
		.orderBy(desc(chapters.seq))
		.limit(1)
		.get();
	const seq = (max?.seq ?? -1) + 1;
	const row = db
		.insert(chapters)
		.values({ uuid: randomUUID(), bookId, seq, title })
		.returning()
		.get();
	return { id: row.id, seq: row.seq };
}

// WRITE UPLOADED PAGE IMAGES TO DISK AND CREATE THEIR DB ROWS. RETURNS THE CREATED PAGE COUNT.
// SEQ CONTINUES AFTER THE CHAPTER'S EXISTING PAGES (A SECOND UPLOAD MUST NOT COLLIDE WITH THE
// (chapterId, seq) UNIQUE INDEX — THE ORIGINAL BUG 500'd EVERY NON-FIRST UPLOAD).
// FILE NAMES ARE UUIDs, NOT seq: seq IS RENUMBERED BY reorder/stitch/delete WHILE FILES KEEP THEIR
// OLD NAMES, SO A SEQ-BASED NAME CAN REUSE A FILE STILL REFERENCED BY ANOTHER PAGE — THE OLD SCHEME
// OVERWROTE THE LAST REMAINING PAGE'S IMAGE ON THE NEXT UPLOAD, MAKING THE LAST TWO PAGES SHOW THE
// SAME PICTURE (EVERY RE-UPLOAD RE-DUPLICATED IT).
// CONVERT ARBITRARY IMAGE BUFFER (PNG/JPEG/AVIF) TO OPTIMIZED WEBP & EXTRACT INTRINSIC DIMENSIONS.
async function convertBufferToWebP(
	buffer: Buffer,
	originalExt: string,
): Promise<{ data: Buffer; ext: string; width: number | null; height: number | null }> {
	try {
		const { loadImage, createCanvas } = await import('@napi-rs/canvas');
		const img = await loadImage(buffer);
		const width = img.width || null;
		const height = img.height || null;
		if (originalExt === '.webp') return { data: buffer, ext: '.webp', width, height };
		const canvas = createCanvas(img.width, img.height);
		const ctx = canvas.getContext('2d');
		ctx.drawImage(img, 0, 0);
		const webpBuf = await canvas.encode('webp', 85);
		return { data: webpBuf, ext: '.webp', width, height };
	} catch {
		// FALLBACK TO ORIGINAL BUFFER IF ENCODER FAILS
		return { data: buffer, ext: originalExt, width: null, height: null };
	}
}

export async function uploadPages(chapterId: number, files: File[]): Promise<number> {
	let count = 0;
	let seq = nextPageSeq(chapterId);
	const uploadDir = join(DATA_ROOT, 'uploads', String(chapterId));
	mkdirSync(uploadDir, { recursive: true });
	for (const file of files) {
		const ext = extname(file.name).toLowerCase();
		if (!ALLOWED_EXT.has(ext)) throw error(400, `Unsupported image type "${ext}" — use PNG/JPEG/WebP/AVIF.`);
		const rawBuf = Buffer.from(await file.arrayBuffer());
		const { data: webpBuf, ext: finalExt, width, height } = await convertBufferToWebP(rawBuf, ext);
		const fileName = `${randomUUID()}${finalExt}`;
		writeFileSync(join(uploadDir, fileName), webpBuf);
		db.insert(pages)
			.values({
				chapterId,
				seq,
				filePath: `uploads/${chapterId}/${fileName}`,
				width: width ?? null,
				height: height ?? null,
			})
			.run();
		seq++;
		count++;
	}
	return count;
}

// THE NEXT FREE SEQ FOR A CHAPTER (PURE DB QUERY — UNIT-TESTED).
export function nextPageSeq(chapterId: number): number {
	const max = db
		.select({ seq: pages.seq })
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(desc(pages.seq))
		.limit(1)
		.get();
	return (max?.seq ?? -1) + 1;
}

// RE-ORDER A CHAPTER'S PAGES (PRESERVES (chapterId, seq) UNIQUE INDEX VIA TEMP SEQUENCES).
export function reorderPages(chapterId: number, pageIds: number[]): void {
	db.transaction(() => {
		for (let i = 0; i < pageIds.length; i++) {
			db.update(pages)
				.set({ seq: -(i + 1000) })
				.where(eq(pages.id, pageIds[i]))
				.run();
		}
		for (let i = 0; i < pageIds.length; i++) {
			db.update(pages)
				.set({ seq: i })
				.where(eq(pages.id, pageIds[i]))
				.run();
		}
	});
}

// MANUALLY STITCH A PAGE WITH THE NEXT PAGE IN THE CHAPTER SEQUENCE.
export async function stitchPageWithNext(
	pageId: number,
	pipeline: PipelineClient,
	dataRoot: string = DATA_ROOT,
): Promise<void> {
	if (!pipeline.stitch) throw new Error('Sidecar stitch operation unavailable.');
	const [topPage] = db.select().from(pages).where(eq(pages.id, pageId)).all();
	if (!topPage) throw new Error('Page not found.');

	const [botPage] = db
		.select()
		.from(pages)
		.where(and(eq(pages.chapterId, topPage.chapterId), eq(pages.seq, topPage.seq + 1)))
		.all();

	if (!botPage) throw new Error('No next page in sequence to stitch with.');

	const topAbs = join(dataRoot, topPage.filePath);
	const botAbs = join(dataRoot, botPage.filePath);

	const topBytes = readFileSync(topAbs);
	const botBytes = readFileSync(botAbs);

	const stitched = await pipeline.stitch(topBytes, botBytes);
	writeFileSync(topAbs, stitched);

	let w: number | null = null;
	let h: number | null = null;
	try {
		const { loadImage } = await import('@napi-rs/canvas');
		const img = await loadImage(stitched);
		w = img.width || null;
		h = img.height || null;
	} catch {
		// ignore
	}

	// RESET TOP PAGE PIPELINE STATE & CLEAR OBSOLETE OUTPUTS
	db.update(pages)
		.set({
			status: 'pending',
			cleanedPath: null,
			outputPath: null,
			error: null,
			width: w,
			height: h,
		})
		.where(eq(pages.id, topPage.id))
		.run();
	db.delete(regions).where(eq(regions.pageId, topPage.id)).run();

	// DELETE BOTTOM PAGE FROM DB & DISK
	db.delete(regions).where(eq(regions.pageId, botPage.id)).run();
	db.delete(pages).where(eq(pages.id, botPage.id)).run();
	try {
		unlinkSync(botAbs);
	} catch {
		// ignore if file missing
	}

	const remainingIds = db
		.select({ id: pages.id })
		.from(pages)
		.where(eq(pages.chapterId, topPage.chapterId))
		.orderBy(pages.seq)
		.all()
		.map((p) => p.id);

	reorderPages(topPage.chapterId, remainingIds);
}

// -- PROGRESS RESET -- //

// CLEAR ONE PAGE'S PIPELINE PROGRESS — DETECTED REGIONS, MEMOIZED TRANSLATIONS (SO A RE-RUN DOES A
// FRESH LLM CALL INSTEAD OF A CACHE HIT), AND OUTPUT PATHS — BACK TO 'pending' FOR A CLEAN RETRY.
// DISK FILES ARE LEFT IN PLACE: THE PIPELINE OVERWRITES clean/ AND output/ ON RE-RUN.
export function resetPageProgress(pageId: number): void {
	db.delete(translations).where(eq(translations.pageId, pageId)).run();
	db.delete(regions).where(eq(regions.pageId, pageId)).run();
	db.update(pages)
		.set({
			status: 'pending',
			cleanedPath: null,
			outputPath: null,
			error: null,
			width: null,
			height: null,
		})
		.where(eq(pages.id, pageId))
		.run();
}

// CLEAR EVERY PAGE OF A CHAPTER. RETURNS HOW MANY PAGES WERE RESET.
export function resetChapterProgress(chapterId: number): number {
	const rows = db.select({ id: pages.id }).from(pages).where(eq(pages.chapterId, chapterId)).all();
	for (const row of rows) resetPageProgress(row.id);
	return rows.length;
}

// SMART RE-SLICE CHAPTER PAGES: COMBINE ALL SLICES, CUT AT NATURAL GUTTERS, AND ATOMICALLY SWAP
export async function resliceChapterPages(
	chapterId: number,
	pipeline: PipelineClient,
	onProgress?: (step: string, message: string, pct: number) => void,
	signal?: AbortSignal,
	dataRoot: string = DATA_ROOT,
): Promise<{ originalCount: number; newCount: number }> {
	if (!pipeline.reslice) throw new Error('Sidecar reslice operation unavailable.');
	const pageRows = db
		.select()
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(pages.seq)
		.all();

	if (pageRows.length === 0) throw error(400, 'Chapter has no pages to reslice.');

	onProgress?.('read', `Reading ${pageRows.length} chapter image slices...`, 15);
	const imageBuffers: Buffer[] = [];
	for (const p of pageRows) {
		signal?.throwIfAborted();
		const absPath = join(dataRoot, p.filePath);
		imageBuffers.push(readFileSync(absPath));
	}

	onProgress?.('reslice', 'Stitching continuous canvas & finding optimal non-text gutters...', 45);
	signal?.throwIfAborted();
	const slicedBuffers = await pipeline.reslice(imageBuffers, signal);
	if (slicedBuffers.length === 0) throw new Error('Reslice produced zero pages.');

	onProgress?.('save', `Writing ${slicedBuffers.length} clean pages and rebuilding database...`, 85);

	const uploadDir = join(dataRoot, 'uploads', String(chapterId));
	mkdirSync(uploadDir, { recursive: true });

	// OLD FILES TO REMOVE AFTER SUCCESS
	const oldFilePaths = pageRows.map((p) => join(dataRoot, p.filePath));

	const newPageRows: { chapterId: number; seq: number; filePath: string; width: number | null; height: number | null }[] = [];
	for (let seq = 0; seq < slicedBuffers.length; seq++) {
		signal?.throwIfAborted();
		const fileName = `${randomUUID()}.png`;
		const absPath = join(uploadDir, fileName);
		writeFileSync(absPath, slicedBuffers[seq]);
		let w: number | null = null;
		let h: number | null = null;
		try {
			const { loadImage } = await import('@napi-rs/canvas');
			const img = await loadImage(slicedBuffers[seq]);
			w = img.width || null;
			h = img.height || null;
		} catch {
			// ignore
		}
		newPageRows.push({
			chapterId,
			seq,
			filePath: `uploads/${chapterId}/${fileName}`,
			width: w,
			height: h,
		});
	}

	// ATOMIC SWAP IN DB
	db.transaction(() => {
		for (const p of pageRows) {
			db.delete(translations).where(eq(translations.pageId, p.id)).run();
			db.delete(regions).where(eq(regions.pageId, p.id)).run();
		}
		db.delete(pages).where(eq(pages.chapterId, chapterId)).run();
		for (const nr of newPageRows) {
			db.insert(pages).values(nr).run();
		}
	});

	// CLEAN UP OLD UPLOADED IMAGE FILES
	for (const oldPath of oldFilePaths) {
		try {
			unlinkSync(oldPath);
		} catch {
			// ignore missing files
		}
	}

	return { originalCount: pageRows.length, newCount: slicedBuffers.length };
}

// PERMANENTLY REMOVE ALL PAGES (IMAGES, REGIONS, TRANSLATIONS) FROM A CHAPTER.
export async function deleteAllChapterPages(
	chapterId: number,
	dataRoot: string = DATA_ROOT,
): Promise<{ deletedCount: number }> {
	const pageRows = db
		.select({ id: pages.id, filePath: pages.filePath })
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.all();

	const oldFilePaths = pageRows.map((p) => join(dataRoot, p.filePath));

	db.transaction(() => {
		for (const p of pageRows) {
			db.delete(translations).where(eq(translations.pageId, p.id)).run();
			db.delete(regions).where(eq(regions.pageId, p.id)).run();
		}
		db.delete(pages).where(eq(pages.chapterId, chapterId)).run();
		db.update(chapters)
			.set({ status: 'pending', translatedAt: null })
			.where(eq(chapters.id, chapterId))
			.run();
	});

	// CANCEL & CLEAR ANY ACTIVE JOBS
	clearChapterJob(chapterId);

	// CLEAN UP OLD UPLOADED IMAGE FILES
	for (const oldPath of oldFilePaths) {
		try {
			unlinkSync(oldPath);
		} catch {
			// ignore missing files
		}
	}

	return { deletedCount: pageRows.length };
}

// PERMANENTLY REMOVE ALL CHAPTERS (AND THEIR PAGES, REGIONS, TRANSLATIONS, FILES) FROM A BOOK.
export async function deleteAllBookChapters(
	bookId: string,
	dataRoot: string = DATA_ROOT,
): Promise<{ deletedCount: number }> {
	const chapterRows = db
		.select({ id: chapters.id })
		.from(chapters)
		.where(eq(chapters.bookId, bookId))
		.all();

	for (const ch of chapterRows) {
		await deleteAllChapterPages(ch.id, dataRoot);
		clearChapterJob(ch.id);
		db.delete(chapters).where(eq(chapters.id, ch.id)).run();
	}

	return { deletedCount: chapterRows.length };
}

// -- CHAPTER READER SSR & API DATA FETCHER -- //

export interface ChapterRegionData {
	id: number;
	seq: number;
	box: unknown;
	textSource: string;
	textTarget: string | null;
	conf: number | null;
}

export interface ChapterPageData {
	id: number;
	seq: number;
	filePath: string;
	cleanedPath: string | null;
	outputPath: string | null;
	status: 'pending' | 'processing' | 'done' | 'error';
	error: string | null;
	width?: number | null;
	height?: number | null;
	regions: ChapterRegionData[];
}

export interface ChapterNavSummary {
	id: number;
	seq: number;
	title: string | null;
	titleTarget?: string | null;
}

export interface ChapterReaderResult {
	chapter: {
		id: number;
		bookId: string;
		seq: number;
		title: string | null;
		titleTarget?: string | null;
		sourceLang: string;
		targetLang: string;
	};
	allChapters: ChapterNavSummary[];
	prevChapter: ChapterNavSummary | null;
	nextChapter: ChapterNavSummary | null;
	pages: ChapterPageData[];
}

function safeJson(raw: string): unknown {
	try {
		return JSON.parse(raw);
	} catch {
		return null;
	}
}

// FETCH COMPLETE CHAPTER READER DATA (USED BY /app/books/[id]/chapters/[chapterId] SSR & API)
export async function getChapterReaderData(chapterId: number): Promise<ChapterReaderResult> {
	await assertChapterExists(chapterId);

	const pageRows = db
		.select()
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(pages.seq)
		.all();

	// SELF-HEALING: EXTRACT AND CACHE DIMENSIONS FOR ANY PAGES MISSING THEM SO SSR HAS EXACT RATIOS
	for (const p of pageRows) {
		if ((p.width === null || p.height === null) && p.filePath) {
			const absPath = join(DATA_ROOT, p.filePath);
			try {
				const buf = readFileSync(absPath);
				const { loadImage } = await import('@napi-rs/canvas');
				const img = await loadImage(buf);
				if (img.width && img.height) {
					p.width = img.width;
					p.height = img.height;
					db.update(pages)
						.set({ width: img.width, height: img.height })
						.where(eq(pages.id, p.id))
						.run();
				}
			} catch {
				// ignore if file is missing or unreadable
			}
		}
	}

	// ALL REGIONS FOR THESE PAGES IN ONE QUERY, GROUPED BY PAGE
	const pageIds = pageRows.map((p) => p.id);
	const regionRows =
		pageIds.length > 0
			? db
					.select()
					.from(regions)
					.where(inArray(regions.pageId, pageIds))
					.orderBy(regions.seq)
					.all()
			: [];
	const byPage = new Map<number, typeof regionRows>();
	for (const r of regionRows) {
		const arr = byPage.get(r.pageId) ?? [];
		arr.push(r);
		byPage.set(r.pageId, arr);
	}

	const chapterRow = db
		.select()
		.from(chapters)
		.where(eq(chapters.id, chapterId))
		.get();

	if (!chapterRow) throw error(404, 'Chapter not found.');

	const bookRow = db.select().from(books).where(eq(books.id, chapterRow.bookId)).get();

	const allChaptersInBook = db
		.select({
			id: chapters.id,
			seq: chapters.seq,
			title: chapters.title,
			titleTarget: chapters.titleTarget,
		})
		.from(chapters)
		.where(eq(chapters.bookId, chapterRow.bookId))
		.orderBy(chapters.seq)
		.all();

	const currentIndex = allChaptersInBook.findIndex((c) => c.id === chapterId);
	const prevChapter = currentIndex > 0 ? allChaptersInBook[currentIndex - 1] : null;
	const nextChapter =
		currentIndex >= 0 && currentIndex < allChaptersInBook.length - 1
			? allChaptersInBook[currentIndex + 1]
			: null;

	return {
		chapter: {
			id: chapterRow.id,
			bookId: chapterRow.bookId,
			seq: chapterRow.seq,
			title: chapterRow.title,
			titleTarget: chapterRow.titleTarget,
			sourceLang: bookRow?.sourceLang || 'zh-CN',
			targetLang: bookRow?.targetLang || 'en',
		},
		allChapters: allChaptersInBook,
		prevChapter,
		nextChapter,
		pages: pageRows.map((p) => ({
			id: p.id,
			seq: p.seq,
			filePath: p.filePath,
			cleanedPath: p.cleanedPath,
			outputPath: p.outputPath,
			status: p.status,
			error: p.error,
			width: p.width,
			height: p.height,
			regions: (byPage.get(p.id) ?? []).map((r) => ({
				id: r.id,
				seq: r.seq,
				box: safeJson(r.box),
				category: (r as any).category ?? 'dialogue',
				textSource: r.textSource,
				textTarget: r.textTarget,
				conf: r.conf,
			})),
		})),
	};
}

