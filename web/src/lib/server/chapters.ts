// CHAPTER / PAGE CREATION HELPERS — SHARED BY THE API ROUTES.
import { randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { join, extname } from 'node:path';
// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { and, desc, eq } from 'drizzle-orm';

// IMPORTED MODULES
import { db } from './db';
import { chapters, pages, regions, translations } from './db/schema';

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
export async function uploadPages(chapterId: number, files: File[]): Promise<number> {
	let count = 0;
	let seq = nextPageSeq(chapterId);
	const uploadDir = join(DATA_ROOT, 'uploads', String(chapterId));
	mkdirSync(uploadDir, { recursive: true });
	for (const file of files) {
		const ext = extname(file.name).toLowerCase();
		if (!ALLOWED_EXT.has(ext)) throw error(400, `Unsupported image type "${ext}" — use PNG/JPEG/WebP/AVIF.`);
		const fileName = `${randomUUID()}${ext}`;
		writeFileSync(join(uploadDir, fileName), Buffer.from(await file.arrayBuffer()));
		db.insert(pages)
			.values({ chapterId, seq, filePath: `uploads/${chapterId}/${fileName}` })
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

	// RESET TOP PAGE PIPELINE STATE & CLEAR OBSOLETE OUTPUTS
	db.update(pages)
		.set({
			status: 'pending',
			cleanedPath: null,
			outputPath: null,
			error: null,
			width: null,
			height: null,
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



